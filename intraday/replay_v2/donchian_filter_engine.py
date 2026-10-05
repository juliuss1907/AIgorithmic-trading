"""Deterministic native-M15 Spot/Short research; no execution adapters or models."""

from bisect import bisect_right
from collections import defaultdict, Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from intraday.replay_v2.donchian_filters import indicator_series, observation, volume_profile, entry_filters
from intraday.replay_v2.donchian_filter_book import FilterBook, ATRTrail
from intraday.replay_v2.donchian_filter_data import WARMUP
from intraday.replay_v2.historical_data import validate_rows
from intraday.replay_v2.historical_mixed import funding_audit, continuous_periods
from intraday.replay_v2.intraday_data import verify_boundaries
from intraday.replay_v2.metrics import fingerprint
from intraday.replay_v2.portfolio_book import ZERO, ONE
from intraday.replay_v2.donchian_spot_gap import (
    SourceSpotCandle, spot_candles, missing_profile_count, stale_symbols, availability,verify_spot_boundaries,
)
from intraday.replay_v2.donchian_mark_gap import (
    SourceMarkCandle, mark_candles, stale_symbols as stale_marks, availability as mark_availability,
)
from intraday.replay_v2.donchian_native_boundaries import POLICY as BOUNDARY_POLICY,verify_perp_boundaries,disclosure


VERSION = 'historical-donchian-filter-study-v1.0'
MS = timedelta(milliseconds=1)
PAIRS = ((20,8),(20,10),(30,8),(30,10))


@dataclass
class Prepared:
    start: object
    end: object
    features: dict
    observations: dict
    h4: dict
    opens: dict
    closes: dict
    settlements: dict
    timeline: list
    checksum: str
    funding_audit: dict
    spot_gap_policy: bool = False
    mark_gap_policy: bool = False
    funding_reference_times: dict | None = None
    native_boundary_policy: str | None = None


def prepare(config, data, funding, *, native_boundary_policy=None):
    if native_boundary_policy not in (None,BOUNDARY_POLICY):
        raise ValueError('unknown native boundary exception policy')
    if set(data) != set(config.weights) or set(funding) != set(config.weights):
        raise ValueError('filter research requires BTC ETH SOL in both markets and funding')
    features, observations, h4 = {}, {}, {}
    opens = {m:defaultdict(dict) for m in ('spot','perp','mark')}
    closes = {m:defaultdict(dict) for m in opens}
    identities = {}
    gap_policy = any(isinstance(b, SourceSpotCandle) for tags in data.values() for b in tags['spot15m'])
    mark_policy = any(isinstance(b, SourceMarkCandle) for tags in data.values() for b in tags['mark15m'])
    for s, series in data.items():
        identities[s] = {}
        if set(series) != {'spot4h','spot15m','perp4h','perp15m','mark15m'}:
            raise ValueError('missing native filter study series')
        for tag, rows in series.items():
            interval = '4h' if tag.endswith('4h') else '15m'
            first = config.start if tag == 'mark15m' else config.start-WARMUP[interval]
            if tag == 'spot15m' and gap_policy:
                spot_candles([b.row() for b in rows], s, first, config.end)
            elif tag == 'mark15m' and mark_policy:
                mark_candles([b.row() for b in rows], s, first, config.end)
            else:
                validate_rows([b.row() for b in rows], interval, first, config.end)
            identities[s][tag] = fingerprint([b.row() for b in rows])
        for market in ('spot','perp'):
            large, small = series[market+'4h'], series[market+'15m']
            checker=verify_spot_boundaries if market=='spot' and gap_policy else verify_boundaries
            if market=='perp' and native_boundary_policy:
                checker=verify_perp_boundaries
            checker(s, large, small, config.start, config.end, market+' H4/M15')
            indicators = indicator_series(large)
            small_times = [b.available_at for b in small]
            features.setdefault(market, {})[s] = {}
            observations.setdefault(market, {})[s] = {}
            h4.setdefault(market, {})[s] = {}
            for i,bar in enumerate(large):
                if not config.start <= bar.available_at < config.end:
                    continue
                f = indicators[i].copy()
                # Profile ends at trigger candle OPEN: never includes trigger volume.
                end = bisect_right(small_times, bar.opened_at)
                begin = bisect_right(small_times, bar.opened_at-timedelta(hours=480))
                window = small[begin:end]
                missing = missing_profile_count(bar.opened_at-timedelta(hours=480), bar.opened_at) if gap_policy and market == 'spot' else 0
                if len(window) != 1920-missing:
                    raise ValueError('profile requires full prior 20-day M15 coverage')
                profile = volume_profile(window)
                f['profile'] = {k:v for k,v in profile.items() if k != 'volumes'} if profile else None
                features[market][s][bar.available_at] = f
                observations[market][s][bar.available_at] = {
                    pair:observation(large[max(0,i-30):i+1], *pair) for pair in PAIRS}
                h4[market][s][bar.available_at] = bar
        for market in opens:
            for bar in series[market+'15m']:
                if config.start <= bar.opened_at < config.end:
                    opens[market][bar.opened_at][s] = bar
                    closes[market][bar.available_at-MS][s] = bar
    audit = funding_audit(config, funding)
    if not all(v['complete'] for v in audit.values()):
        raise ValueError('incomplete funding history cannot support this comparison')
    reference_times={}
    for s,h in funding.items():
        if hasattr(h,'reference_times_ms'):
            reference_times[s]=set(h.reference_times_ms)
            audit[s]['price_reference']=dict(policy=h.data_policy,quotes=len(h.reference_times_ms),
                maximum_age_ms=31,mark_snapshot_id=h.mark_snapshot_id,
                original_rates_and_times=True,settlement_price_not_api_confirmed=True)
    settlements = defaultdict(list)
    for s, history in sorted(funding.items()):
        if history.symbol != s:
            raise ValueError('funding belongs to another coin')
        for row in history.settlements:
            if config.start <= row.at < config.end:
                settlements[row.at].append((s,row))
    timeline = sorted(set().union(*(set(v) for v in (*opens.values(), *closes.values())),
                                 settlements, {config.end}))
    identity=dict(series=identities,funding={s:h.model_dump(mode='json') for s,h in sorted(funding.items())})
    if native_boundary_policy: identity['native_boundary_policy']=native_boundary_policy
    checksum=fingerprint(identity)
    return Prepared(config.start, config.end, features, observations, h4, opens, closes,
                    settlements, timeline, checksum, audit, gap_policy, mark_policy, reference_times,native_boundary_policy)


def position(book, market, symbol):
    return (book.positions if market == 'spot' else book.perps)[symbol]


def close_position(book, market, symbol, at, price, reason):
    (book.close if market == 'spot' else book.close_perp)(symbol, at, price, reason)


def flatten(book, at, bars, closed):
    for market in ('spot','perp'):
        for s,bar in sorted(bars[market].items()):
            if position(book,market,s).quantity:
                close_position(book,market,s,at,bar.open,book.halt_reason)
                closed.add((market,s))


def check_stops(book, at, bars, closed, *, opening):
    for market in ('spot','perp'):
        for s,bar in sorted(bars[market].items()):
            trail = book.trails.get((market,s))
            if not trail:
                continue
            breached = (trail.side*(bar.open-trail.stop) <= 0 if opening else
                        bar.low <= trail.stop if trail.side > 0 else bar.high >= trail.stop)
            if breached:
                price = bar.open if opening else trail.stop
                close_position(book,market,s,at,price,'atr_stop_gap' if opening else 'atr_trailing_touch')
                closed.add((market,s))


def simulate(config, prepared):
    if (config.start, config.end) != (prepared.start,prepared.end):
        raise ValueError('prepared features belong to another window')
    book = FilterBook(config)
    marks = {s:b.open for s,b in prepared.opens['spot'][config.start].items()}
    book.perp_marks = {s:b.open for s,b in prepared.opens['mark'][config.start].items()}
    cooldown = defaultdict(set)
    pending_exits = set()
    observed_spot = {s:config.start for s in marks}
    observed_mark = {s:config.start for s in book.perp_marks}

    def risk(at):
        book.check_isolated_collateral(at)
        book.enforce_risk(at, marks)
        if prepared.spot_gap_policy:
            book.curve[-1].update(stale_spot_symbols=stale_symbols(at, marks),
                spot_observed_at={s:t.isoformat() for s,t in observed_spot.items()})
        if prepared.mark_gap_policy:
            book.curve[-1].update(stale_mark_symbols=stale_marks(at, observed_mark),
                mark_observed_at={s:t.isoformat() for s,t in observed_mark.items()})

    for at in prepared.timeline:
        closed = cooldown.pop(at, set())
        closing = {m:prepared.closes[m].get(at,{}) for m in ('spot','perp')}
        opening = {m:prepared.opens[m].get(at,{}) for m in ('spot','perp')}
        if any(closing.values()) or prepared.closes['mark'].get(at):
            marks.update({s:b.close for s,b in closing['spot'].items()})
            observed_spot.update({s:at for s in closing['spot']})
            book.perp_marks.update({s:b.close for s,b in prepared.closes['mark'].get(at,{}).items()})
            observed_mark.update({s:at for s in prepared.closes['mark'].get(at,{})})
            check_stops(book,at,closing,closed,opening=False)
            cooldown[at+MS].update(closed)
            risk(at)
        if any(opening.values()) or prepared.opens['mark'].get(at):
            marks.update({s:b.open for s,b in opening['spot'].items()})
            observed_spot.update({s:at for s in opening['spot']})
            book.perp_marks.update({s:b.open for s,b in prepared.opens['mark'].get(at,{}).items()})
            observed_mark.update({s:at for s in prepared.opens['mark'].get(at,{})})
        book.advance_day(at,marks)
        for s,row in prepared.settlements.get(at,()):
            book.perp_marks[s] = row.mark
            observed_mark[s] = at
            before=len(book.events)
            book.settle_funding(s,at,row.rate,row.mark)
            if (len(book.events)>before and int(round(at.timestamp()*1000)) in (
                    prepared.funding_reference_times or {}).get(s,set())):
                book.events[-1]['funding_price_source']='native_m15_mark_open_reference_not_api_settlement_quote'
        if prepared.settlements.get(at):
            risk(at)
        if at == config.end:
            last = {m:prepared.closes[m][at-MS] for m in ('spot','perp')}
            for market in last:
                for s,bar in sorted(last[market].items()):
                    close_position(book,market,s,at,bar.close,'window_end')
            risk(at)
            break
        if not any(opening.values()):
            continue
        risk(at)
        if book.halted:
            flatten(book,at,opening,closed)
            risk(at)
        else:
            check_stops(book,at,opening,closed,opening=True)
            if closed:
                risk(at)
                if book.halted:
                    flatten(book,at,opening,closed)
                    risk(at)
        book.maybe_resume(at,marks)
        for market,s in sorted(pending_exits.copy()):
            if s in opening[market]:
                if position(book,market,s).quantity:
                    close_position(book,market,s,at,opening[market][s].open,'donchian_exit')
                    closed.add((market,s))
                    risk(at)
                pending_exits.discard((market,s))
        is_signal_time = at in prepared.features['spot']['BTCUSDT']
        if not is_signal_time:
            continue
        for market,side in (('spot',1),('perp',-1)):
            for s in sorted(config.weights):
                p = position(book,market,s)
                if not p.quantity:
                    continue
                f, bar = prepared.features[market][s][at], prepared.h4[market][s][at]
                obs = prepared.observations[market][s][at][(config.entry_window,config.exit_window)]
                if obs['long_exit' if side > 0 else 'short_exit']:
                    if s in opening[market]:
                        close_position(book,market,s,at,opening[market][s].open,'donchian_exit')
                        closed.add((market,s))
                    else:
                        pending_exits.add((market,s))
                elif bar.opened_at >= p.entered_at:
                    trail = book.trails[(market,s)]
                    before = trail.stop
                    trail.update(bar.high,bar.low,f['atr'])
                    if market == 'perp':
                        book.perps[s].stop = trail.stop
                    if before != trail.stop:
                        book.event(at,'trailing_update','closed_h4_atr14x3',market=market,symbol=s,
                            previous_stop=str(before),stop=str(trail.stop),signal_available_at=at.isoformat())
        # H4 closed immediately before this M15 open. Tightened stop is active
        # now, but cannot be used against the already closed M15/H4 candle.
        check_stops(book,at,opening,closed,opening=True)
        if closed:
            risk(at)
        if book.halted:
            flatten(book,at,opening,closed)
            risk(at)
        for market,side in (('spot',1),('perp',-1)):
            candidates = {}
            for s in sorted(config.weights):
                if s not in opening[market] or position(book,market,s).quantity or (market,s) in closed:
                    continue
                obs = prepared.observations[market][s][at][(config.entry_window,config.exit_window)]
                if not obs['long_entry' if side > 0 else 'short_entry']:
                    continue
                f = prepared.features[market][s][at]
                failed = entry_filters(f,side,config.filter_level,
                                       adx_threshold=getattr(config,'adx_threshold',25))
                if failed:
                    book.event(at,'entry_blocked','indicator_filters',market=market,symbol=s,
                        failed_filters=failed,signal_available_at=at.isoformat())
                    continue
                price, atr = opening[market][s].open, f['atr']
                if atr is None or not ZERO < 3*atr/price < ONE:
                    book.event(at,'entry_blocked','invalid_atr_stop',market=market,symbol=s)
                    continue
                if book.halted:
                    book.event(at,'entry_blocked','portfolio_loss_limit',market=market,symbol=s)
                    continue
                candidates[s] = min(ONE,Decimal('.02')/(atr/f['close']))
            if market == 'spot' and candidates:
                book.enter_batch(at,marks,candidates)
            for s,multiplier in candidates.items():
                f, price = prepared.features[market][s][at], opening[market][s].open
                if market == 'perp':
                    target = book.perp_budget(marks)*config.perp_weights[s]*multiplier
                    if not book.enter_perp(s,at,marks,price,-1,3*f['atr']/price,approved_target=target):
                        continue
                    book.events[-1]['reason'] = 'donchian_short_entry'
                if position(book,market,s).quantity:
                    book.trails[(market,s)] = ATRTrail(side,price,f['atr'])
                    book.event(at,'entry_features','closed_h4',market=market,symbol=s,
                        atr_size_multiplier=str(multiplier),stop=str(book.trails[(market,s)].stop),
                        features=f,signal_available_at=at.isoformat())
                risk(at)
                if book.halted:
                    flatten(book,at,opening,closed)
                    risk(at)
                    break
    return result(config,prepared,book)


def result(config, prepared, book):
    if not book.flat or abs(sum(book.realized_capital.values(),ZERO)-book.cash) > Decimal('1e-18'):
        raise ValueError('filter study must finish flat with reconciled separate capital')
    net = book.cash-config.capital
    if abs(sum((t['net_pnl'] for t in book.trades),ZERO)-net) > Decimal('1e-18'):
        raise ValueError('filter study trade PnL does not reconcile')
    contributions = {}
    for market in ('spot','perp'):
        for s in sorted(config.weights):
            trades = [t for t in book.trades if t['market']==market and t['symbol']==s]
            contributions[market+':'+s] = dict(closed_trades=len(trades), **{
                k:float(sum((t[k] for t in trades),ZERO)) for k in
                ('gross_pnl','exchange_fee','slippage_cost','funding_paid','net_pnl')})
    blockers = Counter(f for e in book.events if e['reason']=='indicator_filters' for f in e['failed_filters'])
    valid = not book.limitations
    payload = {**config.model_dump(mode='json'), 'symbol':'BTC+ETH+SOL','market':'spot+short1x'}
    summary = dict(initial_capital=float(config.capital),final_equity_known=float(book.cash),
        net_pnl=float(net) if valid else None,pnl_after_known_costs=float(net),
        net_return_pct=float(net/config.capital*100) if valid else None,
        max_drawdown_known_pct=float(book.max_drawdown*100), closed_trades=len(book.trades),
        exchange_fee_known=float(book.fees),slippage_cost_known=float(book.slippage),
        funding_paid_known=float(book.funding_paid),funding_complete=True,
        daily_stops=sum(e['kind']=='halt' and e['reason']=='daily_loss_limit' for e in book.events),
        blocked_by_filter=dict(sorted(blockers.items())),contributions=contributions,
        exit_reasons=dict(Counter(t['exit_reason'] for t in book.trades)),
        realized_sizing={'final_capital':{k:float(v) for k,v in book.realized_capital.items()}},
        six_month_periods=continuous_periods(config,book.curve))
    hours = [(datetime.fromisoformat(t['closed_at'])-datetime.fromisoformat(t['opened_at'])).total_seconds()/3600 for t in book.trades]
    summary['holding_hours'] = dict(mean=sum(hours)/len(hours) if hours else None, maximum=max(hours,default=None))
    if prepared.spot_gap_policy:
        summary['spot_availability'] = availability(book.curve)
    if prepared.mark_gap_policy:
        summary['mark_availability'] = mark_availability(book.curve)
    if prepared.funding_reference_times:
        summary['funding_price_reference']={s:prepared.funding_audit[s]['price_reference'] for s in prepared.funding_reference_times}
    if prepared.native_boundary_policy:
        summary['native_boundary_disclosure']=disclosure()
    return dict(schema_version='2',evaluator_version=VERSION,
        result_id=fingerprint(dict(version=VERSION,config=payload,data=prepared.checksum)),
        research_only=True,activation_allowed=False,official_gate_eligible=False,
        status='complete' if valid else 'limited',config=payload,
        inputs=dict(dataset_checksum=prepared.checksum,config_checksum=fingerprint(payload),funding_audit=prepared.funding_audit),
        summary=summary,methodology=dict(
            allocation='60/40 Spot/isolated Short1x; both BTC40 ETH30 SOL30; independent realized sizing, no reserve/transfers',
            signals='closed native H4 Donchian; preceding channel excludes trigger; no D1/Jev/LLM',
            atr='simple TR14 preserves old sizing min(1,.02/ATR%); initial and ratcheting price stop ATRx3',
            indicators='EMA SMA seeds; DMI14/ADX14 Wilder; prior20 volume SMA and strict >1.2x',
            profile='prior120 H4 excluding trigger; native M15 uniform volume in low/high,50 bins,VA70%,POC tie lower/expansion tie upper',
            trailing='closed H4 high/low since entry; new stop effective next M15; old stop evaluated first',
            daily='combined marked start-of-UTC-day equity3%; pending flatten next M15 open; next UTC day AND flat resume',
            fills='H4 signal next H4 open; old stops gap at open or touch priced at stop/logged at M15 close; no same-bar reentry',
            costs=('Spot10/5bps, Perp5/5bps fee/cash-charged slippage each fill; actual funding separate'
                   if getattr(config, 'cost_multiplier', 1) == 1 else
                   'Spot20/10bps, Perp10/10bps fee/cash-charged slippage each fill; actual funding unchanged'),
            drawdown='native M15 marks open/close plus funding/cost; observe-only; not exact tick/intrabar DD',
            **({'funding_pricing':'Original API rates/times; missing settlement quotes use actual native mark open 0-31ms before settlement, explicitly reference not API-confirmed settlement price'}
               if prepared.funding_reference_times else {})),
        limitations=sorted(book.limitations | {'approximate_m15_volume_profile_not_tick_volume_at_price',
            'm15_ohlc_touch_execution_order_unknown', 'daily_gaps_and_exit_costs_can_overshoot',
            'ideal_fractional_fills_without_order_book_or_partial_fills','no_exact_liquidation_or_historical_demo_simulation',
            'window_already_seen_not_untouched_out_of_sample'} | (
                {'spot_source_gap_stale_valuation_and_delayed_execution'} if prepared.spot_gap_policy else set()) | (
                {'mark_source_gap_stale_valuation'} if prepared.mark_gap_policy else set()) | (
                {'missing_funding_settlement_quote_native_mark_open_reference_0_to_31ms'} if prepared.funding_reference_times else set()) | (
                {'exact_whitelisted_native_h4_m15_price_disagreements'} if prepared.native_boundary_policy else set())),
        v1_reference=None,equity_curve=book.curve,trades=book.trades,events=book.events)
