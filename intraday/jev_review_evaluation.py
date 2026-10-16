"""Frozen, small-grid offline evaluation; tuning and test are separate invocations."""

from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from itertools import product
import json
import hashlib
from pathlib import Path

import numpy as np

from intraday import setup2
from intraday.jev_review import database_routes, route_identity, scan_route, valid_plan
from intraday.jev_review_cache import AnswerCache, answer_key, canonical, digest
from intraday.jev_review_counterfactual import supplement_counterfactuals
from intraday.jev_review_overlay import ReviewOverlay, compare_routes
from intraday.portfolio_coordinator import ParentPortfolioPolicy
from intraday.replay_v2.artifacts import publish_report
from intraday.replay_v2.engine import SETUP2_SLOT, _setup2, setup2_splits, simulate
from intraday.replay_v2.ledger import Ledger
from intraday.setup2_review import LabelConfig, QUESTION_SET_VERSION

SPLIT = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROTOCOL = Path(__file__).resolve().parents[1]/'docs/jev-setup2-review-evaluation-protocol-2026-10-16.md'
GRID = tuple(LabelConfig(t_skip=a, t_exh=b, t_reduce=c, t_dis=d)
             for a,b,c,d in product((Decimal('.6'), Decimal('.7')), (Decimal('.6'), Decimal('.7')),
                                    (Decimal('.5'), Decimal('.6')), (Decimal('.5'), Decimal('.6'))))


def thresholds(payload):
    return LabelConfig(**{k: Decimal(v) if k != 'version' else v for k,v in payload.items()})


def period_config(config, period):
    if period not in {'tuning', 'test'}:
        raise ValueError('invalid evaluation period')
    return config.model_copy(update={'start': config.start if period == 'tuning' else max(config.start, SPLIT),
                                    'end': min(config.end, SPLIT) if period == 'tuning' else config.end})


def prepared_book(config, data, prepared):
    """Use the native Setup-2 loop for grid scores, without hashing large datasets 160 times.

    Only the final locked pair is published through normal simulate(). No substitute
    evaluator/result_id is emitted for this optimization. Tuning scores must equal
    the subsequently published normal replay for the selected thresholds.
    """
    with localcontext() as context:
        context.prec = 28
        book = Ledger(
            config, policy=ParentPortfolioPolicy.from_split(*setup2_splits(data.rule), 1), slot_weight=SETUP2_SLOT)
        _setup2(config, data, book, set(), side=1 if config.market == 'spot' else -1, prepared=prepared)
        return book


def aggregate(reports):
    profits = [float(t['net_known_pnl']) for r in reports for t in r['trades']]
    capital = sum(r['config']['capital'] if isinstance(r['config']['capital'], (int,float))
                  else float(r['config']['capital']) for r in reports)
    changes = {}
    for r in reports:
        previous = float(r['config']['capital'])
        for point in r['equity_curve']:
            value = float(point['equity_known'])
            changes[point['at']] = changes.get(point['at'], 0.)+value-previous
            previous = value
    equity = peak = capital
    drawdown = 0.
    for _, delta in sorted(changes.items()):
        equity += delta
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak-equity)/peak if peak else 0)
    return dict(closed_trades=len(profits), net_pnl=sum(r['summary']['net_pnl'] for r in reports),
                initial_capital=capital, max_drawdown_pct=drawdown*100,
                mean_trade_profit=float(np.mean(profits)) if profits else None,
                winrate=sum(p>0 for p in profits)/len(profits) if profits else None)


def bootstrap_difference(baseline, reviewed, *, draws=10000, seed=20261016):
    rng = np.random.default_rng(seed)
    means, totals = [], []
    for reports in (baseline, reviewed):
        summed = np.zeros(draws)
        count = 0
        for report in reports:
            values = np.asarray([float(t['net_known_pnl']) for t in report['trades']])
            if len(values):
                summed += rng.choice(values, size=(draws, len(values)), replace=True).sum(axis=1)
                count += len(values)
        if not count:
            return dict(draws=draws, seed=seed, difference=None, ci95=None, contains_zero=True)
        means.append(summed/count)
        totals.append(sum(float(t['net_known_pnl']) for r in reports for t in r['trades'])/count)
    low, high = np.quantile(means[1]-means[0], [.025, .975])
    return dict(draws=draws, seed=seed, method='independent trade resampling stratified by route and branch',
                difference=totals[1]-totals[0], ci95=[float(low), float(high)], contains_zero=bool(low <= 0 <= high))


def calibration(reports, signals, cache, *, base_rate=None):
    index = {(s.rule_id, s.bar_close.isoformat()): s.tokens for s in signals}
    rows = []
    for report in reports:
        rule = report['inputs']['rule_id']
        for trade in report['trades']:
            state = index.get((rule, trade['entered_at']))
            if state is None:
                raise ValueError('calibration trade missing its rule-qualified signal')
            answer = cache.get(state)
            if answer is None:
                raise ValueError('calibration answer missing')
            rows.append(dict(rule_id=rule, entered_at=trade['entered_at'], key=answer_key(state),
                loss=int(float(trade['net_known_pnl'])<0), skip=float(answer.skip), exhaustion=float(answer.exhaustion)))
    if not rows:
        return dict(samples=0, loss_rate=None, base_rate_forecast=base_rate, questions={}, rows=[])
    rate = sum(r['loss'] for r in rows)/len(rows)
    forecast = rate if base_rate is None else base_rate
    result = dict(samples=len(rows), loss_rate=rate, base_rate_forecast=forecast,
                  base_rate_brier=sum((forecast-r['loss'])**2 for r in rows)/len(rows), questions={}, rows=rows)
    for name in ('skip','exhaustion'):
        bins = []
        for i in range(5):
            selected = [r for r in rows if min(int(r[name]*5),4) == i]
            bins.append(dict(low=i/5, high=(i+1)/5, samples=len(selected),
                mean_probability=sum(r[name] for r in selected)/len(selected) if selected else None,
                loss_rate=sum(r['loss'] for r in selected)/len(selected) if selected else None))
        result['questions'][name] = dict(brier=sum((r[name]-r['loss'])**2 for r in rows)/len(rows), bins=bins)
    return result


def choose(scores):
    return max(scores, key=lambda row: (Decimal(row['net_pnl']),
        *(Decimal(row['thresholds'][k]) for k in ('t_skip','t_exh','t_reduce','t_dis'))))


def write_new(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(canonical(payload)+'\n')


def bound_routes(args, plan, cache):
    if not valid_plan(plan) or plan['question_set_version'] != QUESTION_SET_VERSION:
        raise ValueError('evaluation requires a valid current-question collection plan')
    states = [tuple(s['state']) for s in plan['signals']]
    for state in set(states):
        row = cache._row(state, plan['model'])
        if row is None or json.loads(row['response_json']).get('synthetic_fixture'):
            raise ValueError('evaluation requires complete genuine answers')
    identities, found = [], []
    for config, data in database_routes(args.database, args.end, funding_dir=args.funding_dir):
        print('Preparing bound route:', config.symbol, config.market, flush=True)
        prepared = setup2.series_observations(list(data.candles), list(data.profile_candles),
            side=1 if config.market == 'spot' else -1, anchor=data.indicator_anchor,
            profile=data.rule.parameters.entry_profile, warmup_bars=data.warmup_bars or setup2.WARMUP_BARS,
            own_from=data.price_proxy_until)
        signals = scan_route(config, data, prepared=prepared)
        identity = route_identity(config, data, signals)
        expected = next((r for r in plan['route_inputs'] if
                         (r['symbol'],r['market']) == (config.symbol,config.market)), None)
        if identity != expected:
            raise ValueError('evaluation dataset/config differs from approved plan')
        found.extend(s.payload() for s in signals)
        identities.append(identity)
        yield config, data, prepared, signals
    if identities != plan['route_inputs'] or found != plan['signals']:
        raise ValueError('evaluation signal universe differs from approved plan')


def evaluate(args):
    plan = json.loads(args.approved_plan.read_text())
    protocol_sha = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    lock = None
    tuning = None
    if args.phase == 'test':
        lock = json.loads(args.lock.read_text())
        if digest({k:v for k,v in lock.items() if k != 'lock_hash'}) != lock['lock_hash'] or (
                lock['plan_hash'], lock['protocol_sha256']) != (plan['plan_hash'], protocol_sha):
            raise ValueError('locked config/protocol/plan mismatch')
        if hashlib.sha256(args.tuning_receipt.read_bytes()).hexdigest() != args.expected_tuning_hash:
            raise ValueError('tuning receipt hash mismatch')
        tuning = json.loads(args.tuning_receipt.read_text())
        if tuning['period'] != 'tuning' or tuning['lock_hash'] != lock['lock_hash']:
            raise ValueError('tuning receipt does not match locked config')
    if args.output.exists() or (args.phase == 'tune' and args.lock.exists()):
        raise ValueError('evaluation refuses to overwrite receipts or a locked config')
    args.report_dir.mkdir(parents=True, exist_ok=True)
    with AnswerCache(args.cache) as cache:
        answers_sha = cache.used_digest(tuple(s['state']) for s in plan['signals'])
        if lock is not None and lock['used_answers_sha256'] != answers_sha:
            raise ValueError('answers changed after tuning lock')
        # Materialize and validate the entire signal universe before emitting outcomes.
        routes = list(bound_routes(args, plan, cache))
        if args.phase == 'tune':
            scores = [dict(thresholds=asdict(cfg), net_pnl=Decimal(0), closed_trades=0) for cfg in GRID]
            for config, data, prepared, found in routes:
                config = period_config(config, 'tuning')
                signals = [s for s in found if config.start <= s.bar_close < config.end]
                for cfg, score in zip(GRID, scores):
                    overlay = ReviewOverlay.from_cache(((s.rule_id,s.bar_close,s.tokens) for s in signals), cache, config=cfg)
                    book = prepared_book(config, replace(data,jev_review=overlay), prepared)
                    score['net_pnl'] += book.equity(book.last_mark)-config.capital if book.last_mark else Decimal(0)
                    score['closed_trades'] += len(book.trades)
                print('Tuning grid complete:', config.symbol, config.market, flush=True)
            # Canonicalize Decimal fields so lock hashing is identical after JSON loading.
            scores = json.loads(canonical(scores))
            selected = choose(scores)
            lock = dict(schema_version='jev-review-evaluation-lock-v1', plan_hash=plan['plan_hash'],
                protocol_sha256=protocol_sha, used_answers_sha256=answers_sha, scores=scores,
                selected=selected, tuning_end_exclusive=SPLIT.isoformat(), test_start=SPLIT.isoformat(),
                created_at=datetime.now(timezone.utc).isoformat(), selection='maximum summed tuning net PnL; descending threshold tuple tie-break')
            lock['lock_hash'] = digest(lock)
            write_new(args.lock, lock)  # Persist before any test replay/outcome is read.
            print('Thresholds locked:', canonical(selected), lock['lock_hash'], flush=True)
        cfg = thresholds(lock['selected']['thresholds'])
        baseline, reviewed, comparisons, labels, summaries, signals_all = [], [], [], Counter(), [], []
        period = 'tuning' if args.phase == 'tune' else 'test'
        for config, data, prepared, found in routes:
            config = period_config(config, period)
            signals = [s for s in found if config.start <= s.bar_close < config.end]
            signals_all.extend(signals)
            overlay = ReviewOverlay.from_cache(((s.rule_id,s.bar_close,s.tokens) for s in signals), cache, config=cfg)
            labels.update(row[2] for row in overlay.labels)
            base = simulate(config, data)
            other = simulate(config, replace(data, jev_review=overlay))
            rows = supplement_counterfactuals(config, data, compare_routes(base,other,overlay), base['inputs']['dataset_checksum'])
            saved = {name:publish_report(args.report_dir, report) for name,report in [('rule_only',base),('reviewed',other)]}
            summaries.append(dict(symbol=config.symbol,market=config.market,
                result_ids=dict(rule_only=base['result_id'],reviewed=other['result_id']), reports=saved,
                metrics=dict(rule_only=aggregate([base]),reviewed=aggregate([other])),
                answers_sha256=overlay.answers_sha256))
            comparisons.extend(dict(symbol=config.symbol,market=config.market,**r) for r in rows)
            baseline.append(base)
            reviewed.append(other)
            print('Published locked pair:', period, config.symbol, config.market, flush=True)
        if args.phase == 'tune':
            normal_pnl = sum(Decimal(str(r['summary']['net_pnl'])) for r in reviewed)
            if abs(normal_pnl-Decimal(lock['selected']['net_pnl'])) > Decimal('.000000001'):
                raise ValueError('prepared tuning score disagrees with native replay')
        cal = calibration(baseline, signals_all, cache,
                          base_rate=tuning['calibration']['loss_rate'] if tuning is not None else None)
        result = dict(research_only=True, period=period, plan_hash=plan['plan_hash'], lock_hash=lock['lock_hash'],
            protocol_sha256=protocol_sha, thresholds=asdict(cfg), used_answers_sha256=answers_sha,
            metrics=dict(rule_only=aggregate(baseline), reviewed=aggregate(reviewed)),
            bootstrap=bootstrap_difference(baseline,reviewed), calibration=cal,
            tuning_receipt_sha256=args.expected_tuning_hash if tuning is not None else None,
            labels=dict(labels), routes=summaries, counterfactual=comparisons)
        write_new(args.output, result)
        print('Evaluation receipt:', args.output, flush=True)
