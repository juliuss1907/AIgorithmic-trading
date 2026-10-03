"""Explicit Yahoo collection and immutable offline four-stock research matrix."""

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from intraday.replay_v2.artifacts import _write
from intraday.replay_v2.metrics import encoded, fingerprint
from lab.data import _download_yahoo, _normalize, digest
from lab.equity_research import EquityConfig, prepare, simulate


def freeze_inputs(config, frames):
    prepare(config, frames)
    payload = {'schema_version': '1', 'source': 'Yahoo Finance via yfinance',
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'data_start': str(config.data_start), 'end_exclusive': str(config.end.date()),
        'series': {s: {'dates': [str(d.date()) for d in f.index], 'columns': f.columns.tolist(),
                       'rows': f.values.tolist()} for s, f in sorted(frames.items())}}
    return {**payload, 'checksum': fingerprint(payload)}


def decode_inputs(config, raw):
    if not isinstance(raw, dict) or raw.get('checksum') != fingerprint({k:v for k,v in raw.items() if k != 'checksum'}):
        raise ValueError('equity input checksum mismatch')
    if (raw.get('schema_version'), raw.get('source'), raw.get('data_start'), raw.get('end_exclusive')) != (
        '1', 'Yahoo Finance via yfinance', str(config.data_start), str(config.end.date())):
        raise ValueError('equity input identity/window mismatch')
    series = raw.get('series')
    required = {'open', 'high', 'low', 'close', 'adj_close', 'volume'}
    if not isinstance(series, dict) or set(series) != set(config.weights) or any(
        not isinstance(p, dict) or any(not isinstance(p.get(k), list) for k in ('rows', 'columns', 'dates'))
        or any(not isinstance(c, str) for c in p['columns']) or len(set(p['columns'])) != len(p['columns'])
        or not required <= set(p['columns']) for p in series.values()):
        raise ValueError('equity input series shape/symbols/columns mismatch')
    frames = {s: pd.DataFrame(p['rows'], columns=p['columns'], index=pd.to_datetime(p['dates']))
              for s, p in series.items()}
    prepare(config, frames)
    return frames


def read_inputs(path):
    fd = os.open(Path(path).expanduser().resolve(), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(20_000_001)
    if len(raw) > 20_000_000:
        raise ValueError('equity input bundle exceeds size limit')
    return json.loads(raw)


def collect(config, root, downloader=_download_yahoo, progress=None):
    frames = {}
    for s in config.weights:
        request = SimpleNamespace(symbol=s, start=config.data_start, end_exclusive=config.end.date())
        frames[s] = _normalize(downloader(request))
        # Freeze each returned raw dataset even if a subsequent download fails.
        _write(root/(s+'-raw.json'), encoded({'source': 'Yahoo Finance via yfinance', 'symbol': s,
            'dates': [str(d.date()) for d in frames[s].index], 'columns': frames[s].columns.tolist(),
            'rows': frames[s].values.tolist()}))
        if progress:
            progress({'phase': 'stock_downloaded', 'symbol': s, 'rows': len(frames[s])})
    return freeze_inputs(config, frames)


def comparison_markdown(receipt):
    lines = ['# NVDA / META / MSFT / GOOGL — daily long-only research', '',
        'USD cash account, 1,000 USD initial capital, equal 25% maximum coin budgets, 100% gross entry cap.',
        'Window: '+receipt['window']['start']+' → '+receipt['window']['end']+' (end exclusive).',
        'Rule: Donchian30/8, EMA50+slope, ATR14 simple mean TR; prior close signals, next regular-session open.',
        'Rule sizing: budget × min(1, 2% / ATR%). Stops fixed at entry, not trailing.',
        'Costs assumed: 5bps commission + 5bps cash-charged slippage each fill; not verified broker fees.',
        'Adjusted OHLC uses synthetic total-return units, dividends implicit. No short, leverage, funding or LLM.', '',
        '| Strategy | Stop | Daily loss | Net return | Observed DD | Trades | Stop exits | Research check |',
        '|---|---|---:|---:|---:|---:|---:|---|']
    for r in receipt['results']:
        s = r['summary']
        lines.append(f"| {r['strategy']} | {r['stop'] if r['strategy']=='donchian' else '—'} | "
            f"{r['daily_loss_pct'] if r['strategy']=='donchian' else '—'} | {s['net_return_pct']:.4f}% | "
            f"{s['max_drawdown_pct']:.4f}% | {s['closed_trades']} | {s['stop_exits']} | {s['economic_check_only']} |")
    lines += ['', 'Buy-and-hold is an ungated reference: same capital/costs, no ATR sizing, stops, rebalancing or risk halts.',
        'Daily loss is prior-close to current-session open/close. A close risk halt flattens at next session open;',
        'an opening gap can trigger flattening at that opening price. Daily pause resumes next session AND flat.',
        'DD 10% is terminal; gap/cost overshoot is possible, and the rest of the window can be idle.',
        'All full reports include equity/trade/event journals and per-symbol PnL, reproduced offline.',
        'No simultaneous intraday-low DD, tax/FX/regulatory fees, partial fills or executable historical quote simulation.',
        'Selected surviving stocks, ex-post research; not an independent holdout or an activation gate.',
        'This 1d / 100%-cap stock study is not a like-for-like comparison with the earlier 4h / 60%-cap crypto study.', '']
    return '\n'.join(lines)


def run_study(report_root, *, config=None, collect_data=False, inputs_path=None, raw_inputs=None,
              downloader=_download_yahoo, progress=None):
    config = config or EquityConfig()
    if sum((collect_data, inputs_path is not None, raw_inputs is not None)) != 1:
        raise ValueError('choose exactly one explicit collection or frozen input source')
    root = Path(report_root).expanduser().resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    original_hash = digest(inputs_path) if inputs_path is not None else None
    try:
        raw = collect(config, root, downloader, progress) if collect_data else read_inputs(inputs_path) if inputs_path else raw_inputs
        _write(root/'inputs.json', encoded(raw))
        frames = decode_inputs(config, read_inputs(root/'inputs.json'))
    except (ValueError, OSError) as error:
        _write(root/'collection-error.json', encoded({'status': 'incomplete', 'error_type': type(error).__name__,
                                                     'message': str(error), 'full_return_available': False}))
        raise
    frozen_hash = digest(root/'inputs.json')
    variants = [config.model_copy(update={'stop': stop, 'daily_loss': Decimal(daily)})
                for stop in ('fixed-5pct', 'atr14-3x') for daily in ('.03', '.05')]
    variants.append(config.model_copy(update={'strategy': 'buy-and-hold'}))
    results = []
    for cfg in variants:
        report = simulate(cfg, frames)
        path = root/(report['result_id']+'.json')
        _write(path, encoded(report))
        saved = read_inputs(path)
        restored = EquityConfig.model_validate(saved['config'])
        reproduced = simulate(restored, frames)
        if encoded(saved) != encoded(reproduced):
            raise ValueError('equity full report and journals fail deterministic reproduction')
        row = {'result_id': report['result_id'], 'report': path.name, 'strategy': cfg.strategy,
            'stop': cfg.stop, 'daily_loss_pct': float(cfg.daily_loss*100), 'summary': report['summary'],
            'deterministic_rerun_verified': True, 'dataset_checksum': report['dataset_checksum']}
        results.append(row)
        if progress:
            progress(row)
    if digest(root/'inputs.json') != frozen_hash or inputs_path is not None and digest(inputs_path) != original_hash:
        raise ValueError('immutable stock inputs changed')
    if len({r['result_id'] for r in results}) != 5 or len({r['dataset_checksum'] for r in results}) != 1:
        raise ValueError('five unique equity configs on one dataset required')
    receipt = {'research_only': True, 'activation_allowed': False, 'currency': 'USD',
        'window': {'start': config.start.isoformat(), 'end': config.end.isoformat()},
        'source_unchanged': True, 'snapshot_sha256': frozen_hash, 'source': raw['source'],
        'results': results}
    _write(root/'comparison.json', encoded(receipt))
    _write(root/'comparison.md', comparison_markdown(receipt))
    return receipt


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Research-only daily cash-stock matrix, never activates trading')
    parser.add_argument('--report-root', required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--collect', action='store_true')
    source.add_argument('--inputs', help='Frozen inputs.json for offline reproduction')
    args = parser.parse_args(argv)
    receipt = run_study(args.report_root, collect_data=args.collect, inputs_path=args.inputs,
                        progress=lambda r: print(encoded(r), flush=True))
    print(encoded({'comparison': str(Path(args.report_root).expanduser()/'comparison.md'),
                   'runs': len(receipt['results']), 'source_unchanged': receipt['source_unchanged']}))


if __name__ == '__main__':
    main()
