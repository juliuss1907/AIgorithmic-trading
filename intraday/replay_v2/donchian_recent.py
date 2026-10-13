"""Public-data recent-window research; fresh accounts, immutable inputs, no runtime integration."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import os
from pathlib import Path

from intraday.config import APP_DIRECTORY
from intraday.replay_v2.artifacts import SERIES, _write, publish_report, read_report
from intraday.replay_v2.contracts import utc
from intraday.replay_v2.donchian_adx_config import FiveCoinADXConfig
from intraday.replay_v2.donchian_adx_setups import BasketConfig, H1_BASKET, basket_specification, setup_cases
from intraday.replay_v2.donchian_filter_data import WARMUP, decode_bundle, fetch_spot_snapshot, from_futures
from intraday.replay_v2.donchian_filter_engine import prepare, simulate
from intraday.replay_v2.donchian_oos_pipeline import REPO, git, source_hashes
from intraday.replay_v2.donchian_perp_leverage import PerpLeverageConfig
from intraday.replay_v2.donchian_rotation_config import FOUR_COINS, RotationConfig, equal_weights
from intraday.replay_v2.funding import fetch_funding_snapshot
from intraday.replay_v2.historical_data import fetch_candle_snapshot
from intraday.replay_v2.historical_study import read_inputs
from intraday.replay_v2.metrics import encoded, fingerprint, journal_hash
from intraday.replay_v2.portfolio_study import file_hash

H4 = timedelta(hours=4)
TAGS = ('spot4h', 'spot15m', 'perp4h', 'perp15m', 'mark15m')


def latest_end(now=None):
    at = utc(now or datetime.now(timezone.utc))-timedelta(minutes=5)
    return at.replace(hour=at.hour//4*4, minute=0, second=0, microsecond=0)


def window(*, hours=48, end=None, now=None):
    if isinstance(hours, bool) or not isinstance(hours, int) or hours <= 0 or hours % 4:
        raise ValueError('hours must be a positive multiple of four')
    end = utc(end) if end else latest_end(now)
    start = end-timedelta(hours=hours)
    # Reuse the existing UTC H4 window validation without the prospective module or its constants.
    BasketConfig(start=start, end=end, basket=H1_BASKET, **basket_specification(H1_BASKET))
    return start, end


def recent_cases(start, end):
    cases = [('ADR005-H4', BasketConfig(start=start, end=end, basket=H1_BASKET,
                                      **basket_specification(H1_BASKET)))]
    for name, basket in [('ROT-SLOT', H1_BASKET), ('ZEC-ROT-SLOT', FOUR_COINS)]:
        w = equal_weights(basket)
        cases.append((name, RotationConfig(start=start, end=end, basket=basket, weights=w,
                                          perp_weights=dict(w), position_cap='slot')))
    cases += [(f'PERP{leverage}X', PerpLeverageConfig(start=start, end=end, leverage=leverage))
              for leverage in (1, 5)]
    return cases+setup_cases(start, end)


def bundle_symbols(config):
    return tuple(sorted(set(config.weights) | set(config.perp_weights if config.include_perp else {})))


def symbol_groups(start, end):
    # The five Setup cases use one five-coin prepared feature set, as in donchian_adx_study.
    return (tuple(c+'USDT' for c in H1_BASKET), tuple(c+'USDT' for c in FOUR_COINS),
            tuple(sorted(FiveCoinADXConfig(start=start, end=end).weights)))


def collect(symbols, start, end, output_root, *, now=None, spot_fetcher=fetch_spot_snapshot,
            perp_fetcher=fetch_candle_snapshot, funding_fetcher=fetch_funding_snapshot, progress=print):
    root = Path(output_root).expanduser().resolve()
    if root.exists():
        raise FileExistsError(root)
    start, end = utc(start), utc(end)
    if end <= start or any(t.hour % 4 or t.minute or t.second or t.microsecond for t in (start, end)):
        raise ValueError('collection window must use ordered UTC H4 boundaries')
    if end > latest_end(now):
        raise ValueError('collection end must be an H4 boundary at least five minutes old')
    if tuple(symbols) not in symbol_groups(start, end):
        raise ValueError('collection requires a registered recent-window symbol group')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    raw = dict(schema_version='donchian-filters-1', window=dict(start=start.isoformat(), end=end.isoformat()),
               source_files_sha256={}, candles={}, funding={}, reuse_lineage={})
    for symbol in symbols:
        raw['candles'][symbol], raw['reuse_lineage'][symbol] = {}, {}
        for tag in TAGS:
            interval = '4h' if tag.endswith('4h') else '15m'
            first = start if tag == 'mark15m' else start-WARMUP[interval]
            if tag.startswith('spot'):
                snap = spot_fetcher(symbol, interval, first, end, now=now)
            else:
                kind = 'mark' if tag == 'mark15m' else 'trade'
                snap = from_futures(perp_fetcher(symbol, interval, first, end, price_kind=kind, now=now))
            payload = snap.model_dump(mode='json')
            # Validate even injected fetchers, and preserve completed snapshots if a later fetch fails.
            type(snap).model_validate(payload)
            _write(root/(symbol+'-'+tag+'.json'), encoded(payload))
            raw['candles'][symbol][tag] = payload
            raw['reuse_lineage'][symbol][tag] = dict(parent_snapshot_id=None, active_rows_reused=False,
                                                    supplemental_snapshot_id=snap.snapshot_id)
            progress(f'{symbol} {tag}: {len(snap.raw_rows)} bars')
        raw['funding'][symbol] = funding_fetcher(symbol, start, end, now=now).model_dump(mode='json')
        _write(root/(symbol+'-funding.json'), encoded(raw['funding'][symbol]))
    raw['bundle_checksum'] = fingerprint(raw)
    _write(root/'inputs.json', encoded(raw))
    return root/'inputs.json'


def skipped_signals(report, prepared):
    """Explain early engine skips without altering legacy journals or replay decisions."""
    cfg = report['config']
    seen = {(e['at'], e.get('market'), e.get('symbol')) for e in report['events']
            if e['kind'] in ('entry', 'entry_blocked')}
    skipped = []
    markets = [('spot', 1), ('perp', -1)]
    if cfg.get('universe') == 'perp-leverage':
        markets = [('perp', -1), ('perp', 1)]
    for market, side in markets:
        if market == 'perp' and not cfg['include_perp']:
            continue
        symbols = cfg['weights' if market == 'spot' else 'perp_weights']
        for s in sorted(symbols):
            for at, observations in sorted(prepared.observations[market][s].items()):
                obs = observations[(cfg['entry_window'], cfg['exit_window'])]
                if not obs['long_entry' if side > 0 else 'short_entry'] or (at.isoformat(), market, s) in seen:
                    continue
                cross_market = cfg.get('slots') or (cfg.get('max_positions') and cfg.get('limit_scope', 'total') == 'total')
                related = [t for t in report['trades'] if t['symbol'] == s and (t['market'] == market or cross_market)]
                held = any(datetime.fromisoformat(t['opened_at']) < at < datetime.fromisoformat(t['closed_at'])
                           for t in related)
                cooled = any(datetime.fromisoformat(t['closed_at']) in (at, at-timedelta(milliseconds=1))
                             for t in related)
                if held or cooled:
                    skipped.append(dict(at=at.isoformat(), kind='entry_blocked', market=market, symbol=s, side=side,
                        reason='one_position_per_coin' if held else 'same_bar_reentry',
                        provenance='derived_from_breakout_observation_and_trade_lifetime; filters_not_evaluated'))
    return skipped


def case_detail(report, prepared=None):
    trades, orders, valued = [], [], []
    for trade in report['trades']:
        row = {**trade, 'notional': str(D(trade['quantity'])*D(trade['entry_price'])),
               'valuation_exit': trade['exit_reason'] == 'window_end'}
        trades.append(row)
        common = {k: row[k] for k in ('symbol', 'market', 'side', 'quantity')}
        orders += [dict(common, action='entry', at=row['opened_at'], price=row['entry_price'],
                        notional=row['notional'], reason='donchian_entry', net_pnl=None),
                   dict(common, action='exit', at=row['closed_at'], price=row['exit_price'],
                        notional=str(D(row['quantity'])*D(row['exit_price'])), reason=row['exit_reason'],
                        net_pnl=row['net_pnl'], valuation_exit=row['valuation_exit'])]
        if row['valuation_exit']:
            direction = 1 if row['side'] == 'long' else -1
            gross = direction*D(row['quantity'])*(D(row['exit_price'])-D(row['entry_price']))
            # This is pre-valuation-exit-cost unrealized price PnL; the full trade net includes those costs.
            valued.append(dict(common, entry_price=row['entry_price'], end_price=row['exit_price'],
                               unrealized_price_pnl=str(gross), net_after_assumed_exit_costs=row['net_pnl']))
    derived = skipped_signals(report, prepared) if prepared is not None else []
    reasons = Counter(e['reason'] for e in [*report['events'], *derived] if e['kind'] == 'entry_blocked')
    filters = Counter(f for e in report['events'] if e['kind'] == 'entry_blocked' for f in e.get('failed_filters', []))
    return dict(trades=trades, orders=sorted(orders, key=lambda r: (r['at'], r['action'] == 'entry', r['symbol'])),
                window_end_positions=valued, blocked_entries=dict(sorted(reasons.items())),
                failed_filters=dict(sorted(filters.items())),
                blocked_signals=[e for e in report['events'] if e['kind'] == 'entry_blocked'],
                derived_skipped_signals=derived)


def run(inputs_paths, report_root, *, progress=print):
    root = Path(report_root).expanduser().resolve()
    if root.exists():
        raise FileExistsError(root)
    bundles, lineage = {}, {}
    for path in inputs_paths:
        path = Path(path).expanduser().resolve()
        before, raw = file_hash(path), read_inputs(path)
        if raw['bundle_checksum'] != fingerprint({k: v for k, v in raw.items() if k != 'bundle_checksum'}):
            raise ValueError('recent bundle checksum mismatch')
        if raw.get('native_boundary_policy') or any('data_policy' in p for tags in raw['candles'].values() for p in tags.values()):
            raise ValueError('recent inputs require complete public data without historical gap exceptions')
        key = tuple(sorted(raw['candles']))
        if key in bundles:
            raise ValueError('duplicate recent symbol bundle')
        bundles[key], lineage[str(path)] = raw, dict(sha256=before, bundle_checksum=raw['bundle_checksum'])
    if len(bundles) != 3 or len({encoded(b['window']) for b in bundles.values()}) != 1:
        raise ValueError('three recent bundles must share one window')
    start, end = (datetime.fromisoformat(next(iter(bundles.values()))['window'][k]) for k in ('start', 'end'))
    variants = recent_cases(start, end)
    sources = source_hashes(REPO)
    commit = git('rev-parse', 'HEAD')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    binding = dict(engine_commit=commit, sources=sources, inputs=lineage,
                   configs={n: c.model_dump(mode='json') for n, c in variants})
    _write(root/'binding.json', encoded(binding))
    prepared = {}
    for symbols in symbol_groups(start, end):
        key = tuple(sorted(symbols))
        cfg = (FiveCoinADXConfig(start=start, end=end) if len(symbols) == 5 else
               next(c for _, c in variants if bundle_symbols(c) == key))
        prepared[key] = prepare(cfg, *decode_bundle(cfg, bundles[key]))
    results = []
    for name, cfg in variants:
        symbols = tuple(sorted(FiveCoinADXConfig(start=start, end=end).weights)) if name.startswith('Setup-') else bundle_symbols(cfg)
        dataset = prepared[symbols]
        progress('Replay '+name)
        report = simulate(cfg, dataset)
        saved = publish_report(root/'reports', report)
        repeated = simulate(cfg, dataset)
        loaded = read_report(root/'reports', saved['run_id'])
        if loaded['result_id'] != repeated['result_id'] or encoded(loaded['summary']) != encoded(repeated['summary']):
            raise ValueError('recent summary does not reproduce: '+name)
        hashes = {series: journal_hash(repeated[series]) for series in SERIES}
        if any(file_hash(Path(saved['report_directory'])/(s+'.jsonl')) != hashes[s] for s in SERIES):
            raise ValueError('recent full journal does not reproduce: '+name)
        item = dict(variant=name, **saved, bundle_symbols=list(symbols), dataset_checksum=dataset.checksum,
                    journal_sha256=hashes, deterministic_rerun_verified=True, **case_detail(report, dataset))
        results.append(item)
        _write(root/f'case-{len(results)-1}.json', encoded(item))
    if source_hashes(REPO) != sources or any(file_hash(p) != v['sha256'] for p, v in lineage.items()):
        raise ValueError('recent source or inputs changed during replay')
    receipt = dict(window=dict(start=start.isoformat(), end=end.isoformat()), hours=(end-start).total_seconds()/3600,
        h4_closed_bars=int((end-start)/H4), engine_commit=commit, binding_checksum=fingerprint(binding),
        research_only=True, activation_allowed=False, official_gate_eligible=False,
        deterministic_rerun_verified=True, inputs=lineage, results=results,
        note='Fresh 1000-USDT accounts ignore positions opened before start. window_end exits are valuations, '
             'including assumed closing costs. Small descriptive sample, not strategy evidence. ZEC stands in for HYPE.')
    _write(root/'comparison.json', encoded(receipt))
    _write(root/'comparison.md', markdown(receipt))
    return receipt


def markdown(receipt):
    lines = ['# Recent-window offline replay', '', 'UTC '+receipt['window']['start']+' → '+receipt['window']['end'],
        f"{receipt['h4_closed_bars']} closed H4 bars; commit `{receipt['engine_commit']}`.", '', receipt['note'], '',
        '| Case | Net USDT | Return % | DD % | Trades | Daily locks |', '|---|---:|---:|---:|---:|---:|']
    for row in receipt['results']:
        s = row['summary']
        lines.append(f"| {row['variant']} | {s['pnl_after_known_costs']:.4f} | {s['net_return_pct']} | "
                     f"{s['max_drawdown_known_pct']:.4f} | {s['closed_trades']} | {s['daily_stops']} |")
    lines += ['', 'PERP5X full-size margin per coin is about 1/3 capital, notional 1.67x capital. A 10% price stop',
        'can cost 16.7% of capital per position; daily3% can bind at roughly 1.8% adverse movement for one position',
        'or 0.6% for three. ATR sizing reduces exposure. Fixed maintenance0.4%; hard stop to liquidation9.6pp.',
        'M15 OHLC cannot order simultaneous touches; the model uses liquidation first. No activation.', '', '## Inputs', '']
    for path, identity in receipt['inputs'].items():
        lines += [f"- `{path}`: file SHA256 `{identity['sha256']}`, bundle `{identity['bundle_checksum']}`."]
    for row in receipt['results']:
        lines += ['', '## '+row['variant'], '', f"Report `{row['report_directory']}`; result `{row['result_id']}`.", '',
                  'Blocked entries: `'+encoded(row['blocked_entries'])+'`.',
                  'Failed filters: `'+encoded(row['failed_filters'])+'`.', '',
                  'Early skips reconstructed from breakout observations and trade lifetimes (legacy journals retained): `'+
                  encoded(row['derived_skipped_signals'])+'`.', '',
                  '| UTC | Action | Coin | Market | Side | Price | Quantity | Notional | Reason | Net USDT |',
                  '|---|---|---|---|---|---:|---:|---:|---|---:|']
        for order in row['orders']:
            lines.append('| '+' | '.join(str(order.get(k, '')) for k in
                ('at', 'action', 'symbol', 'market', 'side', 'price', 'quantity', 'notional', 'reason', 'net_pnl'))+' |')
        if not row['orders']:
            lines += ['No orders.']
        lines += ['', 'Positions valued at end: `'+encoded(row['window_end_positions'])+'`.']
        if row['variant'].startswith('PERP'):
            keys = ('daily_stops', 'hard_stop_exits', 'liquidations', 'funding_paid_known', 'exchange_fee_known',
                    'slippage_cost_known', 'max_locked_margin_usdt', 'hard_stop_liquidation_gap_pct')
            lines += ['', 'Perp accounting: `'+encoded({k: row['summary'][k] for k in keys})+'`.']
    return '\n'.join(lines)+'\n'


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Recent public-data offline replay of ten setups; no orders')
    parser.add_argument('--hours', type=int, default=48)
    parser.add_argument('--end', help='Fixed aware UTC H4 boundary, at least five minutes old')
    parser.add_argument('--report-root', required=True)
    args = parser.parse_args(argv)
    start, end = window(hours=args.hours, end=datetime.fromisoformat(args.end) if args.end else None)
    root = Path(args.report_root).expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths = [collect(symbols, start, end, root/'data'/('-'.join(s.removesuffix('USDT') for s in symbols)))
             for symbols in symbol_groups(start, end)]
    result = run(paths, root/'runs')
    print(encoded(dict(window=result['window'], cases=len(result['results']), report_root=str(root/'runs'))))


if __name__ == '__main__':
    main()
