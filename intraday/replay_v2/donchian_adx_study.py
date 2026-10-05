"""Five exploratory A4 variants, full-journal verification and continuous capital."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from intraday.replay_v2.artifacts import _write, publish_report, SERIES
from intraday.replay_v2.donchian_adx_config import ADXStudyConfig, cases
from intraday.replay_v2.donchian_filter_data import decode_bundle, verify_files
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.donchian_oos_analysis import annual_equity
from intraday.replay_v2.donchian_oos_pipeline import source_hashes, git, REPO, verify_item
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.intraday_study import journal_hash
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


def calendar_periods(curve, start, end, capital):
    first = start.replace(month=1 if start.month<=6 else 7,day=1,hour=0,minute=0,second=0,microsecond=0)
    boundaries = [start]
    at = first
    while at < end:
        at = at.replace(month=7) if at.month==1 else at.replace(year=at.year+1,month=1)
        if start < at < end: boundaries.append(at)
    boundaries.append(end)
    observations = [(datetime.fromisoformat(r['at']),Decimal(r['equity_known'])) for r in curve]
    previous = Decimal(capital); result=[]
    for a,b in zip(boundaries,boundaries[1:]):
        values = [v for at,v in observations if a<=at<b or at==end==b]
        peak,dd = previous,Decimal(0)
        for value in values:
            peak=max(peak,value);dd=max(dd,1-value/peak)
        final=values[-1] if values else previous
        full = a.month in (1,7) and a.day==1 and b.month in (1,7) and b.day==1
        result.append(dict(start=a.isoformat(),end=b.isoformat(),full_calendar_half=full,
                           pnl=float(final-previous),return_pct=float((final/previous-1)*100),
                           max_drawdown_pct=float(dd*100)))
        previous=final
    return result


def drawdown_episode(curve, capital):
    peak=Decimal(capital);pi=0;pidx=tidx=0;dd=Decimal(0)
    for i,row in enumerate(curve):
        value=Decimal(row['equity_known'])
        if value>peak:peak=value;pi=i
        current=1-value/peak
        if current>dd:dd=current;pidx=pi;tidx=i
    p,t=curve[pidx],curve[tidx]
    recovered=next((r for r in curve[tidx+1:] if Decimal(r['equity_known'])>=Decimal(p['equity_known'])),None)
    end=recovered['at'] if recovered else curve[-1]['at']
    return dict(peak_at=p['at'],trough_at=t['at'],peak_equity=float(p['equity_known']),
                trough_equity=float(t['equity_known']),max_drawdown_pct=float(dd*100),
                recovered_at=recovered['at'] if recovered else None,
                days_to_trough=(datetime.fromisoformat(t['at'])-datetime.fromisoformat(p['at'])).total_seconds()/86400,
                days_underwater_observed=(datetime.fromisoformat(end)-datetime.fromisoformat(p['at'])).total_seconds()/86400)


def enriched(config, prepared):
    report=simulate(config,prepared)
    report['summary']['six_month_periods']=calendar_periods(report['equity_curve'],config.start,config.end,config.capital)
    report['summary']['annual_marked_equity']=annual_equity(report['equity_curve'],config.capital)
    report['summary']['drawdown_episode']=drawdown_episode(report['equity_curve'],config.capital)
    report['methodology']['adx_dmi']=('Disabled; EMA/volume/profile retained' if config.adx_threshold is None else
        f'Wilder14 ADX strictly >{config.adx_threshold} and rising; DMI correct direction')
    return report


def runtime_hashes():
    return {**source_hashes(REPO),'scripts/replay_donchian_adx.py':file_hash(REPO/'scripts/replay_donchian_adx.py')}


def run_study(inputs_path, report_root, *, resume=False, progress=print):
    source=Path(inputs_path).expanduser().resolve();before=file_hash(source);raw=read_inputs(source)
    start,end=(datetime.fromisoformat(raw['window'][k]) for k in ('start','end'))
    variants=cases(start,end);lineage=raw['source_files_sha256'];verify_files(lineage)
    binding=dict(version='donchian-adx-exploration-1',engine_commit=git('rev-parse','HEAD'),
                 sources=runtime_hashes(),inputs_path=str(source),inputs_sha256=before,
                 configs={name:cfg.model_dump(mode='json') for name,cfg in variants})
    root=Path(report_root).expanduser().resolve()
    if root.exists():
        if not resume:raise FileExistsError(root)
        if read_inputs(root/'binding.json')!=binding:raise ValueError('research source/config/input binding changed')
    else:
        root.mkdir(parents=True,mode=0o700)
        _write(root/'binding.json',encoded(binding))
    progress('Preparing shared causal H4 features and native M15 volume profiles')
    data,funding=decode_bundle(variants[0][1],raw)
    prepared=prepare(variants[0][1],data,funding,native_boundary_policy=raw.get('native_boundary_policy'))
    del data,funding,raw
    results=[]
    for index,(name,config) in enumerate(variants):
        if runtime_hashes()!=binding['sources']:raise ValueError('research engine changed during run')
        checkpoint=root/f'case-{index}.json'
        if checkpoint.exists():
            item=read_inputs(checkpoint)
            if (item['variant']!=name or item['config_checksum']!=fingerprint(config.model_dump(mode='json')) or
                    item['dataset_checksum']!=prepared.checksum):raise ValueError('research case checkpoint binding changed')
            verify_item(item)
            progress('Verified existing case '+name)
        else:
            progress('Replay '+name)
            report=enriched(config,prepared)
            restored=ADXStudyConfig.model_validate({k:v for k,v in report['config'].items() if k in ADXStudyConfig.model_fields})
            repeated=enriched(restored,prepared)
            if (report['result_id']!=repeated['result_id'] or report['summary']!=repeated['summary'] or
                    report['methodology']!=repeated['methodology'] or
                    any(journal_hash(report[s])!=journal_hash(repeated[s]) for s in SERIES)):
                raise ValueError('research full-journal deterministic mismatch')
            saved=publish_report(root/'reports',report)
            item=dict(variant=name,config=report['config'],config_checksum=fingerprint(config.model_dump(mode='json')),
                      run_id=saved['run_id'],report_directory=saved['report_directory'],result_id=report['result_id'],
                      dataset_checksum=prepared.checksum,status=report['status'],summary=report['summary'],
                      deterministic_rerun_verified=True)
            verify_item(item)
            _write(checkpoint,encoded(item))
            progress(encoded(dict(variant=name,net_usdt=item['summary']['net_pnl'],
                dd_pct=item['summary']['max_drawdown_known_pct'],trades=item['summary']['closed_trades'])))
            del report,repeated
        results.append(item)
    verify_files(lineage)
    if file_hash(source)!=before or runtime_hashes()!=binding['sources']:
        raise ValueError('research evidence/source changed')
    receipt=dict(preset='donchian-adx-exploration',window={'start':start.isoformat(),'end':end.isoformat()},
                 research_only=True,research_on_seen_data=True,activation_allowed=False,official_gate_eligible=False,
                 source_unchanged=True,inputs_path=str(source),inputs_sha256=before,
                 source_files_sha256=lineage,binding_checksum=fingerprint(binding),results=results)
    for name,text in [('comparison.json',encoded(receipt)),('comparison.md',markdown(receipt))]:
        path=root/name
        if path.exists():
            if path.read_text()!=text:raise ValueError('existing research comparison changed')
        else:_write(path,text)
    return receipt


def markdown(receipt):
    lines=['# A4 ADX sensitivity — continuous historical capital','',
           'Exploratory already-seen data, not OOS confirmation or trading approval. Times UTC; UI/readout UTC+7.',
           '', '| Case | Final USDT | Net USDT | Return % | DD % | Trades |',
           '|---|---:|---:|---:|---:|---:|']
    for item in receipt['results']:
        s=item['summary'];net=s['net_pnl'];ret=s['net_return_pct']
        lines.append(f"| {item['variant']} | {s['final_equity_known']:.2f} | {net if net is not None else 'UNKNOWN'} | "
                     f"{ret if ret is not None else 'UNKNOWN'} | {s['max_drawdown_known_pct']:.2f} | {s['closed_trades']} |")
    lines+=['','Initial1000USDT, Spot60/Short40, Short1x, BTC40/ETH30/SOL30 each; realized-only reinvestment.',
            'Donchian30/10 H4, EMA200/50, preceding VolumeMA20x1.2 and approximate volume profile retained.',
            'ATR14x3 and ATR sizing unchanged; combined UTC daily3%, DD observe-only.',
            'Enabled ADX is strictly above threshold and rising; DMI correct direction. Off disables both.',
            'Original native prices preserved; audited source gaps/funding references disclosed in each report.',
            'Calendar half-years include explicit partial flags; no capital/position reset at 2024 join.','']
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    from intraday.replay_v2.donchian_adx_data import collect_join
    parser=argparse.ArgumentParser(description='Local five-case A4 ADX exploration; no models or trading')
    parser.add_argument('--report-root',required=True)
    parser.add_argument('--inputs')
    parser.add_argument('--early-inputs');parser.add_argument('--late-inputs')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args(argv)
    root=Path(args.report_root).expanduser().resolve()
    source=args.inputs
    if source is None:
        if not args.early_inputs or not args.late_inputs:parser.error('supply --inputs or both parent inputs')
        source=root/'data'/'inputs.json'
        if not source.exists():source=collect_join(args.early_inputs,args.late_inputs,root/'data')
        elif not args.resume:raise FileExistsError(source)
    run_study(source,root/'runs',resume=args.resume)


if __name__=='__main__':
    main()
