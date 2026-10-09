"""Prospective paper test of the frozen ETH-NEAR-SOL basket (ADR-005); public data only, no orders.

Collection and replay are separate. Every run replays the same frozen rule from the freeze to
the latest published H4 boundary, so each week adds never-before-seen data to one window.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from intraday.config import APP_DIRECTORY
from intraday.notifications import TelegramNotifier
from intraday.replay_v2.artifacts import SERIES, _write, publish_report, read_report
from intraday.replay_v2.donchian_adx_setups import (BasketConfig, BasketStressConfig, basket_specification,
                                                    market_weights)
from intraday.replay_v2.donchian_filter_data import WARMUP, decode_bundle, fetch_spot_snapshot, from_futures
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.funding import fetch_funding_snapshot
from intraday.replay_v2.historical_data import fetch_candle_snapshot
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint, journal_hash
from intraday.replay_v2.portfolio_study import file_hash


# ADR-005 replaces ADR-003 (Setup-2 NEAR/SOL/ZEC, frozen 2026-10-08, never run).
FREEZE = datetime(2026, 10, 10, tzinfo=timezone.utc)
CASE, BASKET = 'ETH-NEAR-SOL', ('ETH', 'NEAR', 'SOL')
# fingerprint of every BasketConfig field except start/end; docs/decisions/005 records it.
RULE_HASH = 'bf6fccc9dc3cc1148c45f42a9b43bff4a80221a9b68230f40a18c6675cd51c88'
# Pre-registered 2026-10-09, before any post-freeze data was replayed.
PREFIX = 'eth-near-sol-prospective-'
CRITERIA = dict(min_days=120, min_trades=60, min_profit_factor=Decimal('1.2'), max_drawdown_pct=Decimal(12))
TAGS = ('spot4h', 'spot15m', 'perp4h', 'perp15m', 'mark15m')
H4 = timedelta(hours=4)


def iso(at):
    return at.isoformat().replace('+00:00', 'Z')


def rule_hash(cfg):
    return fingerprint({k: v for k, v in cfg.model_dump(mode='json').items() if k not in ('start', 'end')})


def config(end, *, start=FREEZE, stress=False):
    cls = BasketStressConfig if stress else BasketConfig
    cfg = cls(start=start, end=end, basket=BASKET, **basket_specification(BASKET))
    if not stress and rule_hash(cfg) != RULE_HASH:
        raise ValueError('prospective rule differs from the frozen '+CASE+' case')
    return cfg


def symbols():
    cfg = config(FREEZE+H4)
    return sorted(set(market_weights(cfg, 'spot')) | set(market_weights(cfg, 'perp')))


def latest_end(now=None, start=FREEZE):
    """Last H4 boundary at least five minutes old, so its final M15 bar is published."""
    now = (now or datetime.now(timezone.utc))-timedelta(minutes=5)
    if now < start+H4:
        raise ValueError('no complete H4 bar after the freeze yet')
    return start+(now-start)//H4*H4


def collect(output_root, end=None, *, start=FREEZE, now=None, spot_fetcher=fetch_spot_snapshot,
            perp_fetcher=fetch_candle_snapshot, funding_fetcher=fetch_funding_snapshot, progress=print):
    """Fetch warmup and post-freeze public data; a gap fails loudly, no historical exceptions apply."""
    root = Path(output_root).expanduser().resolve()
    end = end or latest_end(now, start)
    if end <= start or (end-start) % H4:
        raise ValueError('prospective end must be a later H4 boundary')
    raw = dict(schema_version='donchian-filters-1', window={'start': start.isoformat(), 'end': end.isoformat()},
               source_files_sha256={}, candles={}, funding={}, reuse_lineage={})
    for symbol in symbols():
        raw['candles'][symbol], raw['reuse_lineage'][symbol] = {}, {}
        for tag in TAGS:
            interval = '4h' if tag.endswith('4h') else '15m'
            first = start if tag == 'mark15m' else start-WARMUP[interval]
            if tag.startswith('spot'):
                snap = spot_fetcher(symbol, interval, first, end, now=now)
            else:
                kind = 'mark' if tag == 'mark15m' else 'trade'
                snap = from_futures(perp_fetcher(symbol, interval, first, end, price_kind=kind, now=now))
            raw['candles'][symbol][tag] = snap.model_dump(mode='json')
            raw['reuse_lineage'][symbol][tag] = dict(parent_snapshot_id=None, active_rows_reused=False,
                                                    supplemental_snapshot_id=snap.snapshot_id)
            progress(f'{symbol} {tag}: {len(snap.raw_rows)} bars')
        raw['funding'][symbol] = funding_fetcher(symbol, start, end, now=now).model_dump(mode='json')
    raw['bundle_checksum'] = fingerprint(raw)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    _write(root/'inputs.json', encoded(raw))
    return root/'inputs.json'


def profit_factor(trades):
    pnl = [Decimal(str(t['net_pnl'])) for t in trades]
    loss = -sum((p for p in pnl if p < 0), Decimal(0))
    return sum((p for p in pnl if p > 0), Decimal(0))/loss if loss else None


def evaluate(report, stress):
    """Score one replay against the pre-registered criteria; never an activation decision."""
    trades = report['trades']
    pnl = [Decimal(str(t['net_pnl'])) for t in trades]
    s = report['summary']
    net = Decimal(str(s['pnl_after_known_costs']))
    stress_net = Decimal(str(stress['summary']['pnl_after_known_costs']))
    start, end = (datetime.fromisoformat(report['config'][k]) for k in ('start', 'end'))
    days = Decimal((end-start).total_seconds())/86400
    pf = profit_factor(trades)
    dd = Decimal(str(s['max_drawdown_known_pct']))
    checks = dict(
        profit_factor=pf is not None and pf >= CRITERIA['min_profit_factor'],
        max_drawdown=dd <= CRITERIA['max_drawdown_pct'],
        net_positive=net > 0,
        net_positive_double_cost=stress_net > 0,  # Full replay with doubled fees and slippage.
        reconciled=report['status'] == 'complete' and stress['status'] == 'complete')
    sample = days >= CRITERIA['min_days'] and len(trades) >= CRITERIA['min_trades']
    prospective = start == FREEZE
    verdict = ('not_prospective' if not prospective else 'insufficient_sample' if not sample
               else 'pass' if all(checks.values()) else 'fail')
    f = lambda v: float(v) if v is not None else None
    return dict(case=CASE, rule_hash=RULE_HASH, freeze=FREEZE.isoformat(), window_start=start.isoformat(),
        window_end=end.isoformat(), days=f(days), closed_trades=len(trades),
        window_end_exits=sum(t['exit_reason'] == 'window_end' for t in trades),
        wins=sum(p > 0 for p in pnl), losses=sum(p < 0 for p in pnl),
        winrate_pct=f(Decimal(sum(p > 0 for p in pnl))/len(pnl)*100) if pnl else None,
        profit_factor=f(pf), net_pnl=f(net), net_pnl_double_cost=f(stress_net),
        net_return_pct=f(net/Decimal(str(s['initial_capital']))*100), max_drawdown_pct=f(dd),
        blocked_by_filter=s.get('blocked_by_filter'),
        criteria={k: (f(v) if isinstance(v, Decimal) else v) for k, v in CRITERIA.items()}, checks=checks,
        verdict=verdict, activation_allowed=False,
        note='Open positions are closed at window end each run, so results are marked, not realized, near the end.')


def run(inputs_path, report_root, *, progress=print):
    source = Path(inputs_path).expanduser().resolve()
    raw = read_inputs(source)
    start, end = (datetime.fromisoformat(raw['window'][k]) for k in ('start', 'end'))
    cfg, stress_cfg = config(end, start=start), config(end, start=start, stress=True)
    prepared = prepare(cfg, *decode_bundle(cfg, raw))
    report, stress = simulate(cfg, prepared), simulate(stress_cfg, prepared)
    root = Path(report_root).expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    saved = publish_report(root/'reports', report)
    stress_saved = publish_report(root/'reports', stress)
    repeated = simulate(cfg, prepared)
    loaded = read_report(root/'reports', saved['run_id'])
    if loaded['result_id'] != repeated['result_id'] or loaded['summary'] != repeated['summary']:
        raise ValueError('prospective summary does not reproduce')
    for series in SERIES:
        if file_hash(Path(saved['report_directory'])/(series+'.jsonl')) != journal_hash(repeated[series]):
            raise ValueError('prospective journal does not reproduce: '+series)
    result = dict(evaluate(report, stress), run_id=saved['run_id'], result_id=report['result_id'],
                  stress_run_id=stress_saved['run_id'], inputs_path=str(source), inputs_sha256=file_hash(source),
                  deterministic_rerun_verified=True)
    _write(root/'evaluation.json', encoded(result))
    progress(encoded({k: result[k] for k in ('verdict', 'days', 'closed_trades', 'net_return_pct',
                                              'max_drawdown_pct', 'profit_factor')}))
    return result


def default_reports_root():
    state = os.getenv('XDG_STATE_HOME')
    return (Path(state).expanduser() if state else Path.home()/'.local'/'state')/APP_DIRECTORY/'reports'


def notifier_from_env(environ=None):
    """Same bot as the BTC paper timer (its EnvironmentFile); None when unset."""
    env = os.environ if environ is None else environ
    token, chat = env.get('TELEGRAM_BOT_TOKEN'), env.get('TELEGRAM_CHAT_ID')
    return TelegramNotifier(token=token, chat_id=chat) if token and chat else None


def message(result):
    f = lambda v, spec: format(v, spec) if v is not None else 'n/a'
    return (f"{CASE} paper: {result['verdict']}\n"
            f"{f(result['days'], '.1f')} ngày, {result['closed_trades']} lệnh, winrate {f(result['winrate_pct'], '.1f')}%\n"
            f"PnL {f(result['net_return_pct'], '+.2f')}% (chi phí x2: {f(result['net_pnl_double_cost'], '+.2f')} USDT), "
            f"DD {f(result['max_drawdown_pct'], '.2f')}%, PF {f(result['profit_factor'], '.2f')}")


def notify(notifier, text, progress):
    if notifier is None:
        return
    try:
        notifier.send(text)
    except Exception as exc:  # Fail open: an alert never fails the evaluation.
        progress(f'telegram delivery failed: {type(exc).__name__}')


def scheduled(reports_root=None, *, now=None, progress=print, notifier=None, **fetchers):
    """Timer entry point: one run per published H4 end; a repeat for the same end is a no-op."""
    try:
        result = _scheduled(reports_root, now=now, progress=progress, **fetchers)
    except Exception as exc:
        notify(notifier, f'{CASE} paper: lỗi {type(exc).__name__}: {str(exc)[:300]}', progress)
        raise
    if result is not None and not result.pop('_skipped', False):
        notify(notifier, message(result), progress)
    return result


def _scheduled(reports_root, *, now, progress, **fetchers):
    try:
        end = latest_end(now)
    except ValueError as exc:
        progress(f'skip: {exc}')
        return None
    root = Path(reports_root or default_reports_root()).expanduser().resolve()
    name = PREFIX+end.strftime('%Y%m%dT%H%MZ')
    inputs, report = root/(name+'-inputs'), root/name
    if (report/'evaluation.json').exists():
        progress(f'skip: {report} already evaluated')
        return dict(json.loads((report/'evaluation.json').read_text()), _skipped=True)
    if report.exists():
        raise ValueError(f'incomplete report directory {report}; inspect it before rerunning')
    if not (inputs/'inputs.json').exists():
        if inputs.exists():
            raise ValueError(f'incomplete input directory {inputs}; inspect it before rerunning')
        collect(inputs, end, now=now, progress=progress, **fetchers)
    return run(inputs/'inputs.json', report, progress=progress)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=f'Prospective paper test of frozen {CASE}; no orders')
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('collect', help='Fetch public warmup and post-freeze data into a new input directory')
    c.add_argument('--output-root', required=True)
    c.add_argument('--end', help='Aware ISO H4 boundary; default is the latest published one')
    r = sub.add_parser('evaluate', help='Replay offline and score against pre-registered criteria')
    r.add_argument('--inputs', required=True)
    r.add_argument('--report-root', required=True)
    t = sub.add_parser('scheduled', help='Collect and evaluate up to the latest published H4 end, once per end')
    t.add_argument('--reports-root', help='Default: $XDG_STATE_HOME/aigorithmic-trading/reports')
    args = parser.parse_args(argv)
    if args.command == 'collect':
        collect(args.output_root, datetime.fromisoformat(args.end) if args.end else None)
    elif args.command == 'evaluate':
        run(args.inputs, args.report_root)
    else:
        scheduled(args.reports_root, notifier=notifier_from_env())


if __name__ == '__main__':
    main()
