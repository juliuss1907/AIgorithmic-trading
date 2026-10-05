"""Sealed seven-case research, immutable checkpoints and full ledger verification."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from intraday.replay_v2.artifacts import _write, publish_report, read_report, SERIES
from intraday.replay_v2.donchian_filter_book import FilterConfig
from intraday.replay_v2.donchian_filter_data import decode_bundle, verify_files
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.donchian_oos_config import cases, START, END
from intraday.replay_v2.donchian_oos_evaluator import CRITERIA_PATH, load_criteria, evaluate, verified_receipt, markdown
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.intraday_study import journal_hash
from intraday.replay_v2.metrics import encoded, fingerprint
from intraday.replay_v2.portfolio_study import file_hash


REPO=Path(__file__).resolve().parents[2]


def git(*args,repo=REPO):
    return subprocess.check_output(['git',*args],cwd=repo,text=True).strip()


def strategy_checksum():
    return fingerprint({name:cfg.model_dump(mode='json',exclude={'start','end'}) for name,cfg in cases()})


def source_hashes(repo):
    files=[*sorted((repo/'intraday').rglob('*.py')),
           *sorted((repo/'scripts').glob('*oos*.py')),
           repo/'docs'/'donchian-oos-criteria.json',repo/'uv.lock',repo/'pyproject.toml']
    return {str(p.relative_to(repo)):file_hash(p) for p in files}


def seal(path, *, repo=REPO, reference_path=None):
    repo=Path(repo).resolve()
    if git('status','--porcelain',repo=repo):
        raise ValueError('commit clean research worktree before sealing')
    receipt=dict(version='donchian-oos-freeze-1',engine_commit=git('rev-parse','HEAD',repo=repo),
        sealed_at=datetime.now(timezone.utc).isoformat(),source_hashes=source_hashes(repo),
        criteria_checksum=fingerprint(load_criteria(repo/'docs'/'donchian-oos-criteria.json')),
        strategy_checksum=strategy_checksum(),window={'start':START.isoformat(),'end':END.isoformat()},
        research_only=True,activation_allowed=False,official_gate_eligible=False)
    if reference_path is not None:
        reference=json.loads(Path(reference_path).read_text())
        if reference.get('golden_reference_verified') is not True:
            raise ValueError('golden old-window reference required before fresh collection')
        verified=verified_receipt(reference_path)
        score=evaluate(verified,load_criteria(repo/'docs'/'donchian-oos-criteria.json'))
        if score['verdict']=='Invalid' or score!=reference['evaluation']:
            raise ValueError('old reference scoring must be valid and unchanged')
        receipt['reference_path']=str(Path(reference_path).resolve())
        receipt['reference_sha256']=file_hash(reference_path)
    receipt['seal_checksum']=fingerprint(receipt)
    path=Path(path).expanduser().resolve(); path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    _write(path,encoded(receipt))
    return receipt


def verify_seal(path, *, repo=REPO):
    frozen=read_inputs(path)
    if frozen['seal_checksum'] != fingerprint({k:v for k,v in frozen.items() if k!='seal_checksum'}):
        raise ValueError('seal checksum changed')
    if (source_hashes(Path(repo)) != frozen['source_hashes'] or strategy_checksum()!=frozen['strategy_checksum'] or
            fingerprint(load_criteria(Path(repo)/'docs'/'donchian-oos-criteria.json'))!=frozen['criteria_checksum'] or
            frozen['window'] != {'start':START.isoformat(),'end':END.isoformat()}):
        raise ValueError('sealed code/criteria/strategy changed')
    if 'reference_path' in frozen and file_hash(frozen['reference_path'])!=frozen['reference_sha256']:
        raise ValueError('sealed reference changed')
    return frozen


def publish_once(path,text):
    """Crash-safe immutable completion: verify existing bytes, create only missing files."""
    import hashlib
    path=Path(path)
    if path.exists():
        if path.is_symlink() or file_hash(path)!=hashlib.sha256(text.encode()).hexdigest():
            raise ValueError('existing publication changed: '+str(path))
    else:
        _write(path,text)


def publish_evaluation(root,receipt):
    publish_once(root/'evaluation.json',encoded(receipt['evaluation']))
    publish_once(root/'evaluation.md',markdown(receipt['evaluation']))


def heldout_report(config, prepared, *, reference):
    report=simulate(config,prepared)
    if not reference:
        report['limitations']=sorted(set(report['limitations'])-{'window_already_seen_not_untouched_out_of_sample'} |
                                     {'retrospective_historical_holdout_not_prospective_proof'})
    return report


def verify_golden(item, golden):
    old=next(row for row in golden['results'] if row['variant']==item['variant'])
    report=read_report(Path(old['report_directory']).parent,old['run_id'])
    if item['summary']!=report['summary'] or item['result_id']!=report['result_id'] or item['dataset_checksum']!=old['dataset_checksum']:
        raise ValueError('old golden summary/identity changed: '+item['variant'])
    for series in SERIES:
        if file_hash(Path(item['report_directory'])/(series+'.jsonl'))!=file_hash(Path(old['report_directory'])/(series+'.jsonl')):
            raise ValueError('old golden full journal changed: '+item['variant']+' '+series)


def run(inputs_path,root, *, seal_path=None, reference=False, golden_path=None, resume=False, progress=print):
    frozen=None
    if not reference:
        if seal_path is None:
            raise ValueError('fresh holdout requires a verified seal')
        frozen=verify_seal(seal_path)
        if 'reference_path' not in frozen:
            raise ValueError('seal must bind verified old-window reference')
    source=Path(inputs_path).expanduser().resolve(); before=file_hash(source)
    raw=read_inputs(source)
    start,end=(datetime.fromisoformat(raw['window'][k]) for k in ('start','end'))
    if not reference and (start,end)!=(START,END):
        raise ValueError('wrong frozen holdout window')
    if not reference and read_inputs(source.parent/'binding.json')['seal_checksum']!=frozen['seal_checksum']:
        raise ValueError('collected data does not bind current seal')
    verify_files(raw['source_files_sha256'])
    variants=cases(start,end)
    if reference:
        variants=[(name,FilterConfig.model_validate(cfg.model_dump(exclude={'cost_multiplier'}))
                   if cfg.cost_multiplier==1 else cfg) for name,cfg in variants]
    binding=dict(inputs_path=str(source),inputs_sha256=before,
                 seal_checksum=frozen['seal_checksum'] if frozen else None,
                 strategy_checksum=strategy_checksum(),reference=reference,
                 golden_sha256=file_hash(golden_path) if golden_path else None)
    root=Path(root).expanduser().resolve()
    root.mkdir(parents=True,exist_ok=resume,mode=0o700)
    if (root/'binding.json').exists():
        if not resume or read_inputs(root/'binding.json')!=binding:
            raise ValueError('technical resume binding mismatch')
    else:
        _write(root/'binding.json',encoded(binding))
    if (root/'comparison.json').exists():
        cached=read_inputs(root/'comparison.json')
        verified=verified_receipt(root/'comparison.json')
        score=evaluate(verified,load_criteria())
        if (any(cached.get(k)!=v for k,v in binding.items()) or
                cached['window']!={'start':start.isoformat(),'end':end.isoformat()} or
                cached['evaluation']!=score or score['verdict']=='Invalid'):
            raise ValueError('completed comparison binding/verdict changed')
        if golden_path:
            golden=read_inputs(golden_path)
            for item in cached['results'][:6]:
                verify_golden(item,golden)
        publish_evaluation(root,cached)
        progress('Evaluator verdict FIRST: '+score['verdict'])
        return cached
    data,funding=decode_bundle(variants[0][1],raw)
    progress('Preparing frozen causal features (no performance output)')
    prepared=prepare(variants[0][1],data,funding)
    lineage=raw['source_files_sha256']; del raw,data,funding
    golden=read_inputs(golden_path) if golden_path else None
    results=[]
    for index,(name,config) in enumerate(variants):
        checkpoint=root/f'case-{index}.json'
        if checkpoint.exists():
            item=read_inputs(checkpoint)
            if (item['variant']!=name or item['dataset_checksum']!=prepared.checksum or
                    item['config_checksum']!=fingerprint(config.model_dump(mode='json'))):
                raise ValueError('case resume identity mismatch')
            # Verify all published files before reuse; never rerun to tune results.
            verify_item(item)
        else:
            progress(f'Running {index+1}/7: {name}; then deterministic full-journal check')
            report=heldout_report(config,prepared,reference=reference)
            repeated=heldout_report(config,prepared,reference=reference)
            if (report['result_id']!=repeated['result_id'] or report['summary']!=repeated['summary'] or
                    report['methodology']!=repeated['methodology'] or
                    any(journal_hash(report[s])!=journal_hash(repeated[s]) for s in SERIES)):
                raise ValueError('deterministic full-journal mismatch: '+name)
            saved=publish_report(root/'reports',report)
            item=dict(variant=name,run_id=saved['run_id'],result_id=report['result_id'],
                      dataset_checksum=prepared.checksum,status=report['status'],summary=report['summary'],
                      deterministic_rerun_verified=True,report_directory=saved['report_directory'],
                      config=report['config'],config_checksum=fingerprint(config.model_dump(mode='json')))
            if golden is not None and index<6:
                verify_golden(item,golden)
            _write(checkpoint,encoded(item))
            del report,repeated
        if golden is not None and index<6:
            verify_golden(item,golden)
        results.append(item)
    verify_files(lineage)
    if file_hash(source)!=before:
        raise ValueError('immutable source changed')
    if frozen is not None:
        verify_seal(seal_path)
    receipt=dict(preset='donchian-oos',window={'start':start.isoformat(),'end':end.isoformat()},
                 research_only=True,activation_allowed=False,official_gate_eligible=False,
                 source_unchanged=True,source_files_sha256=lineage,**binding,results=results,
                 golden_reference_verified=bool(reference and golden is not None))
    receipt['evaluation']=evaluate(receipt,load_criteria())
    _write(root/'comparison.json',encoded(receipt))
    verified_receipt(root/'comparison.json')
    publish_evaluation(root,receipt)
    progress('Evaluator verdict FIRST: '+receipt['evaluation']['verdict'])
    return receipt


def verify_item(item):
    # Avoid modifying checkpoint files; the same immutable loader verifies one case.
    from intraday.replay_v2.artifacts import _manifest, _verified_file
    directory=Path(item['report_directory'])
    report=read_report(directory.parent,item['run_id'])
    if (report['summary']!=item['summary'] or report['config']!=item['config'] or report['result_id']!=item['result_id'] or
            report['status']!=item['status'] or report['inputs']['dataset_checksum']!=item['dataset_checksum'] or
            item['deterministic_rerun_verified'] is not True):
        raise ValueError('checkpoint report mismatch')
    _,manifest=_manifest(directory.parent,item['run_id'])
    for series in SERIES:
        with _verified_file(directory/(series+'.jsonl'),manifest['files'][series+'.jsonl']):
            pass


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description='Local sealed Donchian historical research; no execution')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('seal'); p.add_argument('--output',required=True); p.add_argument('--reference',required=True)
    p=sub.add_parser('run'); p.add_argument('--inputs',required=True); p.add_argument('--output-root',required=True)
    p.add_argument('--seal'); p.add_argument('--reference',action='store_true'); p.add_argument('--golden')
    p.add_argument('--resume',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='seal':
        seal(args.output,reference_path=args.reference)
        print('Frozen engine/criteria/strategy; seal saved.')
    else:
        run(args.inputs,args.output_root,seal_path=args.seal,reference=args.reference,
            golden_path=args.golden,resume=args.resume)


if __name__=='__main__':
    main()
