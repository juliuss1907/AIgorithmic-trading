"""Explicit historical collection after freeze; no interpolation or runtime writes."""

from pathlib import Path

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.donchian_filter_data import (WARMUP, FilterSnapshot, fetch_spot_snapshot,
    from_futures, decode_bundle, verify_files)
from intraday.replay_v2.donchian_oos_config import START, END, OOSConfig
from intraday.replay_v2.donchian_oos_pipeline import verify_seal, publish_once
from intraday.replay_v2.donchian_oos_warmup_repair import load_approved
from intraday.replay_v2.donchian_spot_gap import SparseSpotSnapshot, SourceSpotCandle, load_approved as load_spot_gap
from intraday.replay_v2.donchian_mark_gap import SparseMarkSnapshot, load_approved as load_mark_gap
from intraday.replay_v2.donchian_funding_reference import ReferenceFundingSnapshot,load_approved as load_funding_reference
from intraday.replay_v2.funding import FundingSnapshot, fetch_funding_snapshot
from intraday.replay_v2.historical_data import fetch_candle_snapshot
from intraday.replay_v2.historical_mixed import funding_audit
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


def quality(config,data,funding):
    audit=funding_audit(config,funding)
    if not all(value['complete'] for value in audit.values()):
        raise ValueError('funding gap exceeds 8h+1s: '+encoded(audit))
    series={}
    for symbol,tags in data.items():
        series[symbol]={tag:dict(bars=len(rows),zero_volume=sum(r.volume==0 for r in rows)
                               if tag!='mark15m' else None) for tag,rows in tags.items()}
    basis={}
    for symbol,tags in data.items():
        spot={row.opened_at:row.close for row in tags['spot15m'] if row.opened_at>=config.start}
        perp={row.opened_at:row.close for row in tags['perp15m'] if row.opened_at>=config.start}
        ratios=[(at,abs(perp[at]/value-1)) for at,value in spot.items()]
        basis[symbol]=dict(maximum_absolute_basis_pct=float(max(v for _,v in ratios)*100),
                          over_5pct=[dict(at=at.isoformat(),absolute_basis_pct=float(v*100)) for at,v in ratios if v>.05])
    sparse=any(isinstance(b,SourceSpotCandle) for tags in data.values() for b in tags['spot15m'])
    return dict(strict_checks=('passed: explicit audited Spot M15 gaps only; all other bars dense; finite/OHLC/native boundaries' if sparse else
        'passed: no missing/duplicate/nonfinite bars, OHLC bounds, native H4/M15 boundaries'),
        funding=audit,series=series,basis=basis,
        anomalies_are_flags_not_deleted=True,data_repairs=[],
        descriptive_event_months={'2022-05':'LUNA','2022-11':'FTX','2023-03':'USDC depeg'})


def collect(seal_path,root, *, resume=False, progress=print, spot_fetcher=fetch_spot_snapshot,
            perp_fetcher=fetch_candle_snapshot,funding_fetcher=fetch_funding_snapshot,approved_warmup_audit=None,
            approved_spot_gap_audit=None,approved_mark_gap_audit=None,reuse_root=None,approved_funding_reference_audit=None):
    frozen=verify_seal(seal_path)
    if 'reference_path' not in frozen:
        raise ValueError('old golden reference must be sealed before new data collection')
    config=OOSConfig(start=START,end=END)
    binding=dict(seal_checksum=frozen['seal_checksum'],window={'start':START.isoformat(),'end':END.isoformat()})
    approved,repairs,original_hashes=({},[],{})
    if approved_warmup_audit is not None:
        approved,repairs,original_hashes=load_approved(approved_warmup_audit,START,END)
        binding['approved_warmup_audit_sha256']=file_hash(approved_warmup_audit)
    sparse={}; gap_log=None
    if approved_spot_gap_audit is not None:
        sparse,gap_log,hashes=load_spot_gap(approved_spot_gap_audit,START,END)
        original_hashes.update(hashes)
        binding['approved_spot_gap_audit_sha256']=file_hash(approved_spot_gap_audit)
    sparse_marks={}; mark_log=None
    if approved_mark_gap_audit is not None:
        sparse_marks,mark_log,hashes=load_mark_gap(approved_mark_gap_audit,START,END)
        original_hashes.update(hashes)
        binding['approved_mark_gap_audit_sha256']=file_hash(approved_mark_gap_audit)
    approved_funding={}
    if approved_funding_reference_audit is not None:
        if not sparse_marks:
            raise ValueError('funding reference requires checksum-bound audited native marks')
        approved_funding,hashes=load_funding_reference(approved_funding_reference_audit,START,END,sparse_marks)
        original_hashes.update(hashes)
        binding['approved_funding_reference_audit_sha256']=file_hash(approved_funding_reference_audit)
    reuse=Path(reuse_root).expanduser().resolve() if reuse_root is not None else None
    binding['reuse_root']=str(reuse) if reuse else None
    root=Path(root).expanduser().resolve()
    root.mkdir(parents=True,exist_ok=resume,mode=0o700)
    if (root/'binding.json').exists():
        if not resume or read_inputs(root/'binding.json')!=binding:
            raise ValueError('collection resume seal/window mismatch')
    else:
        _write(root/'binding.json',encoded(binding))
    if reuse is not None:
        manifest=root/'reuse-files.json'
        if manifest.exists():
            reuse_hashes=read_inputs(manifest)
        else:
            reuse_hashes={str(p):file_hash(p) for symbol in sorted(config.weights)
                for tag in ('spot4h','spot15m','perp4h','perp15m','mark15m')
                if (p:=reuse/(symbol+'-'+tag+'.json')).exists()}
            _write(manifest,encoded(reuse_hashes))
        verify_files(reuse_hashes)
        original_hashes.update(reuse_hashes)
        original_hashes[str(manifest)]=file_hash(manifest)
    if approved:
        publish_once(root/'repairs.json',encoded(repairs))
        original_hashes[str(root/'repairs.json')]=file_hash(root/'repairs.json')
    if sparse:
        publish_once(root/'spot-availability.json',encoded(gap_log))
        original_hashes[str(root/'spot-availability.json')]=file_hash(root/'spot-availability.json')
    if sparse_marks:
        publish_once(root/'mark-availability.json',encoded(mark_log))
        original_hashes[str(root/'mark-availability.json')]=file_hash(root/'mark-availability.json')
    if (root/'inputs.json').exists():
        raw=read_inputs(root/'inputs.json'); verify_files(raw['source_files_sha256'])
        decode_bundle(config,raw)
        return root/'inputs.json'
    raw=dict(schema_version='donchian-filters-1',window=binding['window'],source_files_sha256=original_hashes,
             candles={},funding={},reuse_lineage={},data_repairs=repairs)
    for symbol in sorted(config.weights):
        raw['candles'][symbol]={}; raw['reuse_lineage'][symbol]={}
        for tag in ('spot4h','spot15m','perp4h','perp15m','mark15m'):
            interval='4h' if tag.endswith('4h') else '15m'
            start=START if tag=='mark15m' else START-WARMUP[interval]
            path=root/(symbol+'-'+tag+'.json')
            model=(SparseSpotSnapshot if tag=='spot15m' and sparse else
                   SparseMarkSnapshot if tag=='mark15m' and sparse_marks else FilterSnapshot)
            cache=reuse/path.name if reuse is not None else None
            cached=(model.model_validate(read_inputs(cache)) if cache is not None and str(cache) in reuse_hashes else None)
            if path.exists():
                snap=model.model_validate(read_inputs(path))
                if cached is not None and snap.snapshot_id!=cached.snapshot_id:
                    raise ValueError('reused checkpoint changed original cache')
            elif cached is not None:
                snap=cached
            else:
                progress('Collect '+symbol+' '+tag+' (raw history only; no strategy)')
                snap=(approved[symbol] if tag=='spot4h' and approved else
                      sparse[symbol] if tag=='spot15m' and sparse else
                      sparse_marks[symbol] if tag=='mark15m' and sparse_marks else
                      spot_fetcher(symbol,interval,start,END) if tag.startswith('spot') else
                      from_futures(perp_fetcher(symbol,interval,start,END,
                                   price_kind='mark' if tag=='mark15m' else 'trade')))
                _write(path,encoded(snap.model_dump(mode='json')))
            market='spot' if tag.startswith('spot') else 'mark' if tag=='mark15m' else 'perp'
            if (snap.symbol,snap.market,snap.interval,snap.coverage_start,snap.coverage_end)!=(symbol,market,interval,start,END):
                raise ValueError('checkpoint native snapshot identity mismatch')
            if tag=='spot4h' and approved and snap.snapshot_id!=approved[symbol].snapshot_id:
                raise ValueError('normalized checkpoint changed approved source evidence')
            if tag=='spot15m' and sparse and snap.snapshot_id!=sparse[symbol].snapshot_id:
                raise ValueError('gap-aware checkpoint changed approved source evidence')
            if tag=='mark15m' and sparse_marks and snap.snapshot_id!=sparse_marks[symbol].snapshot_id:
                raise ValueError('mark checkpoint changed approved source evidence')
            if not path.exists():
                _write(path,encoded(snap.model_dump(mode='json')))
            raw['candles'][symbol][tag]=snap.model_dump(mode='json')
            raw['reuse_lineage'][symbol][tag]=dict(parent_snapshot_id=cached.snapshot_id if cached is not None else None,
                                                 active_rows_reused=cached is not None,
                                                 supplemental_snapshot_id=snap.snapshot_id)
            raw['source_files_sha256'][str(path)]=file_hash(path)
            progress(symbol+' '+tag+': '+str(len(snap.raw_rows))+' native bars verified')
        path=root/(symbol+'-funding.json')
        model=ReferenceFundingSnapshot if approved_funding else FundingSnapshot
        snap=(model.model_validate(read_inputs(path)) if path.exists() else
              approved_funding[symbol] if approved_funding else funding_fetcher(symbol,START,END))
        if approved_funding and snap.funding_id!=approved_funding[symbol].funding_id:
            raise ValueError('derived funding checkpoint changed approved source evidence')
        if not path.exists():
            _write(path,encoded(snap.model_dump(mode='json')))
        raw['funding'][symbol]=snap.model_dump(mode='json')
        raw['source_files_sha256'][str(path)]=file_hash(path)
    raw['source_files_sha256'][str(root/'binding.json')]=file_hash(root/'binding.json')
    raw['bundle_checksum']=fingerprint(raw)
    data,funding=decode_bundle(config,raw)
    qa=quality(config,data,funding)
    qa['data_repairs']=repairs
    if sparse:
        qa['spot_availability']=gap_log
    if sparse_marks:
        qa['mark_availability']=mark_log
        qa['strict_checks']+='; explicitly approved one-bar mark gap only; trade bars and funding rates/times unchanged'
    if approved_funding:
        qa['funding_price_reference']={s:dict(policy=snap.data_policy,quotes=len(snap.quote_lineage),
            maximum_age_ms=max(q['age_ms'] for q in snap.quote_lineage),mark_snapshot_id=snap.mark_snapshot_id,
            original_rates_and_times=True,original_raw_rows_preserved=True,settlement_price_not_api_confirmed=True)
            for s,snap in approved_funding.items()}
    verify_seal(seal_path); verify_files(raw['source_files_sha256'])
    publish_once(root/'qa.json',encoded(qa))
    raw['source_files_sha256'][str(root/'qa.json')]=file_hash(root/'qa.json')
    raw['bundle_checksum']=fingerprint({k:v for k,v in raw.items() if k!='bundle_checksum'})
    _write(root/'inputs.json',encoded(raw))
    progress('Collection and strict QA complete; no strategy results inspected.')
    return root/'inputs.json'


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description='Collect sealed native Binance OOS history; public read-only')
    p.add_argument('--seal',required=True); p.add_argument('--output-root',required=True)
    p.add_argument('--approved-warmup-audit',help='Explicit opt-in to the three checksum-bound closeTime repairs')
    p.add_argument('--approved-spot-gap-audit',help='Explicit opt-in to audited Spot M15 source gaps; no synthetic bars')
    p.add_argument('--approved-mark-gap-audit',help='Explicit opt-in to audited one-bar mark gap; stale observed mark only')
    p.add_argument('--reuse-root',help='Read-only checkpoint cache; same native identity/coverage/checksum required')
    p.add_argument('--approved-funding-reference-audit',help='Explicit funding price-reference opt-in; actual native mark opens within31ms')
    p.add_argument('--resume',action='store_true'); args=p.parse_args(argv)
    collect(args.seal,args.output_root,resume=args.resume,approved_warmup_audit=args.approved_warmup_audit,
            approved_spot_gap_audit=args.approved_spot_gap_audit,approved_mark_gap_audit=args.approved_mark_gap_audit,
            reuse_root=args.reuse_root,approved_funding_reference_audit=args.approved_funding_reference_audit)


if __name__=='__main__':
    main()
