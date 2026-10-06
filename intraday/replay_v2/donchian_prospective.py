"""Prospective paper test of the frozen A4-Donchian30-10 case; public data only, no orders.

Collection and replay are separate. Collection keeps the frozen bundle's warmup rows before
the freeze and fetches only data after it; replay always starts at the freeze, so every
weekly run re-evaluates the same rule on a longer, never-before-seen window.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from intraday.replay_v2.artifacts import SERIES, _write, publish_report, read_report
from intraday.replay_v2.donchian_filter_book import FilterConfig
from intraday.replay_v2.donchian_filter_data import (SOURCES, WARMUP, FilterSnapshot, decode_bundle,
                                                     fetch_spot_snapshot, from_futures, snapshot)
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.funding import fetch_funding_snapshot
from intraday.replay_v2.historical_data import fetch_candle_snapshot
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint, journal_hash
from intraday.replay_v2.portfolio_study import file_hash


FREEZE = datetime(2026, 10, 2, tzinfo=timezone.utc)
CASE = 'A4-Donchian30-10'
RULE = dict(entry_window=30, exit_window=10, filter_level=4)
# fingerprint of every FilterConfig field except start/end; docs/decisions/002 records it.
RULE_HASH = '4a8eae77d90bd8a3e207f558ecae700e6b4fd4f4672d358d7a26e55a7b21096b'
# Pre-registered 2026-10-06, before any post-freeze data was replayed.
CRITERIA = dict(min_days=120, min_trades=60, min_profit_factor=Decimal('1.2'), max_drawdown_pct=Decimal(12))
TAGS = ('spot4h', 'spot15m', 'perp4h', 'perp15m', 'mark15m')
H4 = timedelta(hours=4)


def iso(at):
    return at.isoformat().replace('+00:00', 'Z')


def config(end):
    cfg = FilterConfig(start=FREEZE, end=end, **RULE)
    if rule_hash(cfg) != RULE_HASH:
        raise ValueError('prospective rule differs from the frozen A4-Donchian30-10 case')
    return cfg


def rule_hash(cfg):
    return fingerprint({k: v for k, v in cfg.model_dump(mode='json').items() if k not in ('start', 'end')})


def latest_end(now=None):
    """Last H4 boundary at least five minutes old, so its final M15 bar is published."""
    now = (now or datetime.now(timezone.utc))-timedelta(minutes=5)
    return FREEZE+(now-FREEZE)//H4*H4


def stitch(base, first, suffix, end):
    """Frozen rows in [first, FREEZE) followed by fetched rows in [FREEZE, end)."""
    if base.coverage_start > first or base.coverage_end < FREEZE:
        raise ValueError('frozen bundle does not cover the warmup before the freeze')
    lo, freeze = int(first.timestamp()*1000), int(FREEZE.timestamp()*1000)
    prefix = tuple(row for row in base.raw_rows if lo <= row[0] < freeze)
    if suffix.coverage_start != FREEZE or (suffix.market, suffix.symbol, suffix.interval) != (
            base.market, base.symbol, base.interval):
        raise ValueError('post-freeze data does not continue the frozen series')
    return snapshot(market=base.market, symbol=base.symbol, interval=base.interval, source=SOURCES[base.market],
        coverage_start=iso(first), coverage_end=iso(end), fetched_at=iso(max(base.fetched_at, suffix.fetched_at)),
        pages=base.pages+suffix.pages, raw_rows=prefix+suffix.raw_rows)


def collect(base_inputs, output_root, end=None, *, now=None, spot_fetcher=fetch_spot_snapshot,
            perp_fetcher=fetch_candle_snapshot, funding_fetcher=fetch_funding_snapshot, progress=print):
    base_path, root = Path(base_inputs).expanduser().resolve(), Path(output_root).expanduser().resolve()
    base = read_inputs(base_path)
    if base.get('bundle_checksum') != fingerprint({k: v for k, v in base.items() if k != 'bundle_checksum'}):
        raise ValueError('frozen filter bundle checksum mismatch')
    if datetime.fromisoformat(base['window']['end']) != FREEZE:
        raise ValueError('frozen bundle must end exactly at the freeze')
    end = end or latest_end(now)
    if end <= FREEZE or (end-FREEZE) % H4:
        raise ValueError('prospective end must be a later H4 boundary')
    raw = dict(schema_version='donchian-filters-1', window={'start': FREEZE.isoformat(), 'end': end.isoformat()},
               source_files_sha256={str(base_path): file_hash(base_path)}, candles={}, funding={}, reuse_lineage={})
    for symbol, series in sorted(base['candles'].items()):
        raw['candles'][symbol], raw['reuse_lineage'][symbol] = {}, {}
        for tag in TAGS:
            frozen = FilterSnapshot.model_validate(series[tag])
            interval = '4h' if tag.endswith('4h') else '15m'
            first = FREEZE if tag == 'mark15m' else FREEZE-WARMUP[interval]
            if tag.startswith('spot'):
                fresh = spot_fetcher(symbol, interval, FREEZE, end, now=now)
            else:
                kind = 'mark' if tag == 'mark15m' else 'trade'
                fresh = from_futures(perp_fetcher(symbol, interval, FREEZE, end, price_kind=kind, now=now))
            joined = stitch(frozen, first, fresh, end)
            raw['candles'][symbol][tag] = joined.model_dump(mode='json')
            raw['reuse_lineage'][symbol][tag] = dict(frozen_snapshot_id=frozen.snapshot_id,
                fetched_snapshot_id=fresh.snapshot_id, frozen_rows=len(joined.raw_rows)-len(fresh.raw_rows),
                fetched_rows=len(fresh.raw_rows))
            progress(f'{symbol} {tag}: {len(fresh.raw_rows)} new bars')
        raw['funding'][symbol] = funding_fetcher(symbol, FREEZE, end, now=now).model_dump(mode='json')
    raw['bundle_checksum'] = fingerprint(raw)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    _write(root/'inputs.json', encoded(raw))
    return root/'inputs.json'


def evaluate(report):
    """Score one replay against the pre-registered criteria; never an activation decision."""
    trades = report['trades']
    pnl = [Decimal(str(t['net_pnl'])) for t in trades]
    gain = sum((p for p in pnl if p > 0), Decimal(0))
    loss = -sum((p for p in pnl if p < 0), Decimal(0))
    costs = sum((Decimal(str(t['exchange_fee']))+Decimal(str(t['slippage_cost'])) for t in trades), Decimal(0))
    s = report['summary']
    net = Decimal(str(s['pnl_after_known_costs']))
    start, end = (datetime.fromisoformat(report['config'][k]) for k in ('start', 'end'))
    days = Decimal((end-start).total_seconds())/86400
    pf = gain/loss if loss else None
    dd = Decimal(str(s['max_drawdown_known_pct']))
    checks = dict(
        profit_factor=pf is not None and pf >= CRITERIA['min_profit_factor'],
        max_drawdown=dd <= CRITERIA['max_drawdown_pct'],
        net_positive=net > 0,
        net_positive_double_cost=net-costs > 0,  # Adds each fill's fee and slippage once more; not a re-replay.
        reconciled=report['status'] == 'complete')
    sample = days >= CRITERIA['min_days'] and len(trades) >= CRITERIA['min_trades']
    verdict = 'insufficient_sample' if not sample else 'pass' if all(checks.values()) else 'fail'
    f = lambda v: float(v) if v is not None else None
    return dict(case=CASE, rule_hash=RULE_HASH, freeze=FREEZE.isoformat(), window_end=end.isoformat(),
        days=f(days), closed_trades=len(trades), window_end_exits=sum(t['exit_reason'] == 'window_end' for t in trades),
        wins=sum(p > 0 for p in pnl), losses=sum(p < 0 for p in pnl),
        winrate_pct=f(Decimal(sum(p > 0 for p in pnl))/len(pnl)*100) if pnl else None,
        profit_factor=f(pf), net_pnl=f(net), net_pnl_double_cost_estimate=f(net-costs),
        net_return_pct=f(net/Decimal(str(s['initial_capital']))*100), max_drawdown_pct=f(dd),
        criteria={k: (f(v) if isinstance(v, Decimal) else v) for k, v in CRITERIA.items()}, checks=checks,
        verdict=verdict, activation_allowed=False,
        note='Open positions are closed at window end each run, so results are marked, not realized, near the end.')


def run(inputs_path, report_root, *, progress=print):
    source = Path(inputs_path).expanduser().resolve()
    raw = read_inputs(source)
    cfg = config(datetime.fromisoformat(raw['window']['end']))
    prepared = prepare(cfg, *decode_bundle(cfg, raw))
    report = simulate(cfg, prepared)
    root = Path(report_root).expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    saved = publish_report(root/'reports', report)
    repeated = simulate(cfg, prepared)
    loaded = read_report(root/'reports', saved['run_id'])
    if loaded['result_id'] != repeated['result_id'] or loaded['summary'] != repeated['summary']:
        raise ValueError('prospective summary does not reproduce')
    for series in SERIES:
        if file_hash(Path(saved['report_directory'])/(series+'.jsonl')) != journal_hash(repeated[series]):
            raise ValueError('prospective journal does not reproduce: '+series)
    result = dict(evaluate(report), run_id=saved['run_id'], result_id=report['result_id'],
                  inputs_path=str(source), inputs_sha256=file_hash(source), deterministic_rerun_verified=True)
    _write(root/'evaluation.json', encoded(result))
    progress(encoded({k: result[k] for k in ('verdict', 'days', 'closed_trades', 'net_return_pct',
                                              'max_drawdown_pct', 'profit_factor')}))
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Prospective paper test of frozen A4-Donchian30-10; no orders')
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('collect', help='Fetch public post-freeze data into a new input directory')
    c.add_argument('--base-inputs', required=True, help='Frozen Donchian filter inputs.json ending at the freeze')
    c.add_argument('--output-root', required=True)
    c.add_argument('--end', help='Aware ISO H4 boundary; default is the latest published one')
    r = sub.add_parser('evaluate', help='Replay offline and score against pre-registered criteria')
    r.add_argument('--inputs', required=True)
    r.add_argument('--report-root', required=True)
    args = parser.parse_args(argv)
    if args.command == 'collect':
        collect(args.base_inputs, args.output_root, datetime.fromisoformat(args.end) if args.end else None)
    else:
        run(args.inputs, args.report_root)


if __name__ == '__main__':
    main()
