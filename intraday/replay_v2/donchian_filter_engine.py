"""Deterministic native-M15 Spot/Short research; no execution adapters or models."""

from bisect import bisect_right
from collections import defaultdict, Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from decimal import Decimal

from intraday.replay_v2.donchian_filters import indicator_series, observation, volume_profile, entry_filters
from intraday.replay_v2.donchian_filter_book import FilterBook, SlotBook, RotationBook, ATRTrail
from intraday.replay_v2.donchian_filter_data import WARMUP
from intraday.replay_v2.donchian_rotation_select import (Candidate, Held, adx_of, pnl_gate_blocks, select_entries,
                                                         trend_strength, volume_ratio)
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
from intraday.replay_v2.donchian_adx_boundaries import (
    POLICY as JOIN_POLICY, verify_join_boundaries, disclosure as join_disclosure,
)
from intraday.replay_v2 import donchian_five_policy as five_policy
from intraday.replay_v2.donchian_adx_setups import market_weights, adx_threshold, allocation_text


VERSION = 'historical-donchian-filter-study-v1.1'
MS = timedelta(milliseconds=1)
PAIRS = ((20,8),(20,10),(30,8),(30,10))
OBSERVED_PAIRS = PAIRS+((30,40),(25,10))  # Extra pairs: the H1 geometry candidate and the 25-bar entry study; PAIRS keeps the 20-case matrix.


def indicator_settings(config):
    return (getattr(config,'bar_interval','4h'), getattr(config,'ema_fast',50),
            getattr(config,'ema_slow',200), getattr(config,'regime_interval',None),
            getattr(config,'adx_period',14), getattr(config,'warmup_hours',484))


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
    spot_gap_policy: frozenset = frozenset()
    mark_gap_policy: frozenset = frozenset()
    funding_reference_times: dict | None = None
    native_boundary_policy: str | None = None
    five_source_symbols: frozenset = frozenset()
    bar_interval: str = '4h'  # Signal-bar width; `h4` keeps its name for the default study.
    derivation: dict | None = None
    ema_periods: tuple = (50, 200)  # (fast, slow)
    regime_interval: str | None = None  # '4h': EMA/ADX/DMI come from the last closed H4 bar.
    adx_period: int = 14  # Wilder smoothing of DMI/ADX only; ATR14 and volume MA20 never move.
    warmup_hours: int = 484  # H1 bars before the window start; H4 and M15 warmups are fixed.


SIGNAL_HOURS = {'4h': 4, '1h': 1}
WARMUP_BY_INTERVAL = {**WARMUP, '1h': WARMUP['15m']}
PROFILE_BARS = 120  # Prior signal bars in the volume profile; 20 days at H4, 5 days at H1.


def prepare(config, data, funding, *, native_boundary_policy=None, derivation=None):
    if native_boundary_policy not in (None,BOUNDARY_POLICY,JOIN_POLICY,five_policy.POLICY):
        raise ValueError('unknown native boundary exception policy')
    signal = getattr(config, 'bar_interval', '4h')
    if signal not in SIGNAL_HOURS or (signal != '4h' and (derivation is None or native_boundary_policy)):
        raise ValueError('hourly signal bars require an audited M15 derivation and no H4 boundary policy')
    lookback = timedelta(hours=PROFILE_BARS*SIGNAL_HOURS[signal])
    profile_m15 = int(lookback/timedelta(minutes=15))
    _, ema_fast, ema_slow, regime, adx_period, warmup_hours = indicator_settings(config)
    if regime not in (None, '4h') or (regime and (signal != '1h' or (ema_fast, ema_slow) != (50, 200) or adx_period != 14)):
        raise ValueError('an H4 regime gate needs H1 bars with the default indicator settings')
    if signal == '4h' and warmup_hours != 484:
        raise ValueError('H4 signal bars use the fixed warmup')
    warmup = {**WARMUP_BY_INTERVAL, '1h': timedelta(hours=warmup_hours)}
    extra = {'spot4h','perp4h'} if regime else set()
    symbols = set(config.weights) | set(market_weights(config, 'perp'))
    if set(data) != symbols or set(funding) != symbols:
        raise ValueError('filter research requires configured coins in both markets and funding')
    features, observations, h4 = {}, {}, {}
    opens = {m:defaultdict(dict) for m in ('spot','perp','mark')}
    closes = {m:defaultdict(dict) for m in opens}
    identities = {}
    gap_policy = frozenset(s for s, tags in data.items() if any(isinstance(b, SourceSpotCandle) for b in tags['spot15m']))
    mark_policy = frozenset(s for s, tags in data.items() if any(isinstance(b, SourceMarkCandle) for b in tags['mark15m']))
    for s, series in data.items():
        identities[s] = {}
        if set(series) != {'spot'+signal,'spot15m','perp'+signal,'perp15m','mark15m'}|extra:
            raise ValueError('missing native filter study series')
        for tag, rows in series.items():
            if not rows:
                raise ValueError('missing native filter study series rows')
            interval = '15m' if tag.endswith('15m') else tag[-2:]
            first = config.start if tag == 'mark15m' else config.start-warmup[interval]
            if tag == 'spot15m' and s in gap_policy:
                checker=five_policy.spot_candles if isinstance(rows[0],five_policy.FiveSpotCandle) else spot_candles
                checker([b.row() for b in rows], s, first, config.end)
            elif tag == 'mark15m' and s in mark_policy:
                checker=five_policy.mark_candles if isinstance(rows[0],five_policy.FiveMarkCandle) else mark_candles
                checker([b.row() for b in rows], s, first, config.end)
            elif interval == '1h' and signal == '1h':
                # Derived from the validated M15 series by the hourly builder, which owns gap checks.
                if rows[0].opened_at != first or rows[-1].available_at != config.end:
                    raise ValueError('hourly signal bars do not cover the study window')
            else:
                validate_rows([b.row() for b in rows], interval, first, config.end)
            identities[s][tag] = fingerprint([b.row() for b in rows])
        for market in ('spot','perp'):
            large, small = series[market+signal], series[market+'15m']
            checker=verify_spot_boundaries if market=='spot' and s in gap_policy else verify_boundaries
            if market=='perp' and native_boundary_policy:
                checker=verify_join_boundaries if native_boundary_policy==JOIN_POLICY else verify_perp_boundaries
            if native_boundary_policy==five_policy.POLICY and (market=='perp' or s in five_policy.SYMBOLS):
                checker=five_policy.verify_five_boundaries
            if signal == '4h':  # Hourly bars are built from these same M15 rows, so the check would be circular.
                checker(s, large, small, config.start, config.end, market+' H4/M15')
            indicators = indicator_series(large, ema_fast=ema_fast, ema_slow=ema_slow, adx_period=adx_period)
            if regime:
                gate = series[market+'4h']
                gate_features, gate_times = indicator_series(gate), [b.available_at for b in gate]
            small_times = [b.available_at for b in small]
            features.setdefault(market, {})[s] = {}
            observations.setdefault(market, {})[s] = {}
            h4.setdefault(market, {})[s] = {}
            for i,bar in enumerate(large):
                if not config.start <= bar.available_at < config.end:
                    continue
                f = indicators[i].copy()
                if regime:
                    # Only an H4 bar already closed at this H1 close may speak for the regime.
                    j = bisect_right(gate_times, bar.available_at)-1
                    if j < 0:
                        raise ValueError('H4 regime warmup does not cover the first signal bar')
                    f.update({k:gate_features[j][k] for k in
                              ('ema50','prior_ema50','ema200','adx','prior_adx','plus_di','minus_di')})
                    f['regime_available_at'] = gate_times[j].isoformat()
                # Profile ends at trigger candle OPEN: never includes trigger volume.
                end = bisect_right(small_times, bar.opened_at)
                begin = bisect_right(small_times, bar.opened_at-lookback)
                window = small[begin:end]
                missing = missing_profile_count(bar.opened_at-lookback, bar.opened_at) if s in gap_policy and market == 'spot' else 0
                if len(window) != profile_m15-missing:
                    raise ValueError('profile requires full prior M15 coverage')
                profile = volume_profile(window)
                f['profile'] = {k:v for k,v in profile.items() if k != 'volumes'} if profile else None
                features[market][s][bar.available_at] = f
                observations[market][s][bar.available_at] = {
                    pair:observation(large[max(0,i-40):i+1], *pair) for pair in OBSERVED_PAIRS}
                h4[market][s][bar.available_at] = bar
        for market in opens:
            for bar in series[market+'15m']:
                if config.start <= bar.opened_at < config.end:
                    opens[market][bar.opened_at][s] = bar
                    closes[market][bar.available_at-MS][s] = bar
    audit = funding_audit(config.model_copy(update={'perp_weights': dict.fromkeys(symbols, ONE)}), funding)
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
    if signal != '4h': identity.update(bar_interval=signal, derivation=derivation)
    if (ema_fast, ema_slow) != (50, 200) or regime: identity.update(ema_periods=[ema_fast, ema_slow], regime_interval=regime)
    if adx_period != 14 or warmup_hours != 484: identity.update(adx_period=adx_period, warmup_hours=warmup_hours)
    checksum=fingerprint(identity)
    return Prepared(config.start, config.end, features, observations, h4, opens, closes,
                    settlements, timeline, checksum, audit, gap_policy, mark_policy, reference_times,native_boundary_policy,
                    frozenset(s for s,tags in data.items() if isinstance(tags['spot15m'][0],five_policy.FiveSpotCandle)),
                    signal, derivation, (ema_fast, ema_slow), regime, adx_period, warmup_hours)


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
                hard = trail.hard is not None and trail.stop == trail.hard
                close_position(book,market,s,at,price,
                               ('hard_stop_gap' if hard else 'atr_stop_gap') if opening else
                               ('hard_stop_touch' if hard else 'atr_trailing_touch'))
                closed.add((market,s))


def simulate(config, prepared):
    if (config.start, config.end) != (prepared.start,prepared.end):
        raise ValueError('prepared features belong to another window')
    if indicator_settings(config) != (prepared.bar_interval, *prepared.ema_periods, prepared.regime_interval,
                                      prepared.adx_period, prepared.warmup_hours):
        raise ValueError('prepared features belong to another indicator configuration')
    slots = getattr(config, 'slots', None)
    rotating = getattr(config, 'max_positions', None)
    scope = getattr(config, 'limit_scope', 'total')  # 'total': one pool of positions; 'sleeve': a limit per sleeve.
    hard_stop = getattr(config, 'hard_stop_pct', None)
    book = RotationBook(config) if rotating else SlotBook(config) if slots else FilterBook(config)
    label = 'h4' if prepared.bar_interval == '4h' else 'h1'
    stop = getattr(config, 'stop_atr_multiple', 3)
    ema = dict(ema_fast=prepared.ema_periods[0], ema_slow=prepared.ema_periods[1])
    active = {m: market_weights(config, m) for m in ('spot', 'perp')}
    if any(not set(active[m]) <= set(prepared.features[m]) for m in active):
        raise ValueError('prepared data is missing configured market/coins')

    def selected(series, market, at):
        symbols = active['perp' if market == 'mark' else market]
        return {s: b for s, b in series[market].get(at, {}).items() if s in symbols}

    marks = {s:b.open for s,b in selected(prepared.opens, 'spot', config.start).items()}
    book.perp_marks = {s:b.open for s,b in selected(prepared.opens, 'mark', config.start).items()}
    cooldown = defaultdict(set)
    pending_exits = set()
    observed_spot = {s:config.start for s in marks}
    observed_mark = {s:config.start for s in book.perp_marks}

    def risk(at):
        book.check_isolated_collateral(at)
        book.enforce_risk(at, marks)
        if prepared.spot_gap_policy:
            old_symbols=(prepared.spot_gap_policy-prepared.five_source_symbols) & active['spot'].keys()
            book.curve[-1].update(stale_spot_symbols=sorted(stale_symbols(at, old_symbols)+
                five_policy.stale_symbols(at,prepared.five_source_symbols & active['spot'].keys())),
                spot_observed_at={s:t.isoformat() for s,t in observed_spot.items()})
        if prepared.mark_gap_policy:
            book.curve[-1].update(stale_mark_symbols=stale_marks(at, {s: observed_mark[s] for s in prepared.mark_gap_policy if s in active['perp']}),
                mark_observed_at={s:t.isoformat() for s,t in observed_mark.items()})

    def collect(at, market, side, opening, closed):
        """Entries this bar would take in one market, before any slot limit."""
        candidates = {}
        for s in sorted(active[market]):
            if s not in opening[market] or position(book,market,s).quantity or (market,s) in closed:
                continue
            if (slots or (rotating and scope == 'total')) and any(
                    position(book,m,s).quantity for m in ('spot','perp') if m != market):
                continue  # One position per coin across both markets.
            obs = prepared.observations[market][s][at][(config.entry_window,config.exit_window)]
            if not obs['long_entry' if side > 0 else 'short_entry']:
                continue
            f = prepared.features[market][s][at]
            failed = entry_filters(f,side,config.filter_level,
                                   adx_threshold=adx_threshold(config,market,s),**ema)
            if failed:
                book.event(at,'entry_blocked','indicator_filters',market=market,symbol=s,
                    failed_filters=failed,signal_available_at=at.isoformat())
                continue
            price, atr = opening[market][s].open, f['atr']
            if atr is None or not ZERO < stop*atr/price < ONE:
                book.event(at,'entry_blocked','invalid_atr_stop',market=market,symbol=s)
                continue
            if book.halted:
                book.event(at,'entry_blocked','portfolio_loss_limit',market=market,symbol=s)
                continue
            candidates[s] = min(ONE,Decimal('.02')/(atr/f['close']))
        return candidates

    def grant_slots(at, opening, closed):
        """Pool both markets' entries, rank them and keep as many as there are free slots."""
        pooled = {m: collect(at,m,side,opening,closed) for m,side in (('spot',1),('perp',-1))}
        entries = [(m,s,mult) for m in ('spot','perp') for s,mult in pooled[m].items()]
        granted = {'spot':{}, 'perp':{}}
        if not entries:
            return granted
        open_now = sum(1 for m in ('spot','perp') for s in active[m] if position(book,m,s).quantity)
        free = max(0, slots-open_now)

        def score(entry):
            f = prepared.features[entry[0]][entry[1]][at]
            ratio = f['volume']/f['volume_ma'] if f['volume_ma'] else ZERO
            return (f['adx'] if f['adx'] is not None else ZERO), ratio

        if getattr(config,'ranker','adx_volume') == 'random':
            seed = getattr(config,'seed','')
            order = sorted(entries,key=lambda e:hashlib.sha256(f'{seed}|{at.isoformat()}|{e[0]}|{e[1]}'.encode()).hexdigest())
        else:
            order = sorted(entries,key=lambda e:(-score(e)[0],-score(e)[1],e[1],e[0]))
        chosen, rejected, taken = [], [], set()
        for entry in order:  # Best first; a coin never takes two slots, even across its two markets.
            if len(chosen) < free and entry[1] not in taken:
                chosen.append(entry)
                taken.add(entry[1])
            else:
                rejected.append(entry)
        book.event(at,'selection','slot_ranking',ranker=getattr(config,'ranker','adx_volume'),
            open_positions=open_now,free_slots=free,
            ranked=[dict(market=m,symbol=s,adx=str(score((m,s,x))[0]),volume_ratio=str(score((m,s,x))[1]),
                         granted=(m,s,x) in chosen) for m,s,x in order])
        for m,s,x in chosen:
            granted[m][s] = x
        for m,s,_ in rejected:
            book.event(at,'entry_blocked','slot_limit',market=m,symbol=s)
        return granted

    def strength(market, symbol, side, at):
        """Trend strength of a coin at this bar: ADX when DMI still points the position's way, else zero."""
        return trend_strength(prepared.features[market][symbol][at], side)

    def open_book_pnl():
        total = ZERO
        for m in ('spot','perp'):
            for s in active[m]:
                p = position(book,m,s)
                if p.quantity:
                    mark = marks[s] if m == 'spot' else book.perp_marks[s]
                    total += p.quantity*(mark-p.entry_price)
        return total

    def grant_rotation(at, opening, closed):
        """Rank this bar's entries; use free positions first, then rotate out the weakest open position."""
        pooled = {m: collect(at,m,side,opening,closed) for m,side in (('spot',1),('perp',-1))}
        entries = [(m,s,mult) for m in ('spot','perp') for s,mult in pooled[m].items()]
        granted = {'spot':{}, 'perp':{}}
        if not entries:
            return granted
        held = [(m,s) for m in ('spot','perp') for s in sorted(active[m]) if position(book,m,s).quantity]
        pnl = open_book_pnl()
        if pnl_gate_blocks(len(held), pnl, pnl_gate=config.pnl_gate):
            for m,s,_ in entries:
                book.event(at,'entry_blocked','pnl_gate',market=m,symbol=s,open_positions=len(held),
                           open_book_pnl=str(pnl))
            return granted
        side_of = {'spot':1,'perp':-1}

        def candidate(entry):
            f = prepared.features[entry[0]][entry[1]][at]
            return Candidate(entry[0], entry[1], entry[2], adx_of(f), volume_ratio(f))

        def holding(m, s):
            p = position(book,m,s)
            priced = s in opening[m]
            return Held(m, s, strength(m,s,side_of[m],at),
                        p.quantity*(opening[m][s].open-p.entry_price) if priced else ZERO, priced)

        steps = select_entries([candidate(e) for e in entries], [holding(m,s) for m,s in held],
                               max_positions=config.max_positions, limit_scope=scope,
                               rotation=config.rotation != 'off', margin=Decimal(config.rotation_margin))
        decisions = []
        for step in steps:
            m, s = step.market, step.symbol
            if step.kind == 'blocked':
                if step.reason == 'rotation_margin':
                    book.event(at,'entry_blocked','rotation_margin',market=m,symbol=s,candidate_adx=str(step.adx),
                               weakest=f'{step.weakest[0]}:{step.weakest[1]}',
                               weakest_strength=str(step.victim_strength))
                else:
                    book.event(at,'entry_blocked',step.reason,market=m,symbol=s)
            elif step.kind == 'free':
                granted[m][s] = step.multiplier
                decisions.append(dict(market=m,symbol=s,adx=str(step.adx),action='free_position'))
            else:
                vm, vs = step.victim
                book.event(at,'rotation','close_weakest_for_new_signal',market=m,symbol=s,candidate_adx=str(step.adx),
                           closed_market=vm,closed_symbol=vs,closed_strength=str(step.victim_strength))
                close_position(book,vm,vs,at,opening[vm][vs].open,'rotation_exit')
                closed.add(step.victim)
                risk(at)
                if book.halted:
                    flatten(book,at,opening,closed)
                    risk(at)
                    return {'spot':{}, 'perp':{}}
                granted[m][s] = step.multiplier
                decisions.append(dict(market=m,symbol=s,adx=str(step.adx),action='rotated',closed=f'{vm}:{vs}'))
        book.event(at,'selection','rotation_ranking',limit_scope=scope,open_positions=len(held),
                   open_book_pnl=str(pnl),decisions=decisions)
        return granted

    for at in prepared.timeline:
        closed = cooldown.pop(at, set())
        closing = {m:selected(prepared.closes, m, at) for m in ('spot','perp')}
        opening = {m:selected(prepared.opens, m, at) for m in ('spot','perp')}
        closing_marks = selected(prepared.closes, 'mark', at)
        opening_marks = selected(prepared.opens, 'mark', at)
        if any(closing.values()) or closing_marks:
            marks.update({s:b.close for s,b in closing['spot'].items()})
            observed_spot.update({s:at for s in closing['spot']})
            book.perp_marks.update({s:b.close for s,b in closing_marks.items()})
            observed_mark.update({s:at for s in closing_marks})
            check_stops(book,at,closing,closed,opening=False)
            cooldown[at+MS].update(closed)
            risk(at)
        if any(opening.values()) or opening_marks:
            marks.update({s:b.open for s,b in opening['spot'].items()})
            observed_spot.update({s:at for s in opening['spot']})
            book.perp_marks.update({s:b.open for s,b in opening_marks.items()})
            observed_mark.update({s:at for s in opening_marks})
        book.advance_day(at,marks)
        settlements = [(s, row) for s, row in prepared.settlements.get(at,()) if s in active['perp']]
        for s,row in settlements:
            book.perp_marks[s] = row.mark
            observed_mark[s] = at
            before=len(book.events)
            book.settle_funding(s,at,row.rate,row.mark)
            if (len(book.events)>before and int(round(at.timestamp()*1000)) in (
                    prepared.funding_reference_times or {}).get(s,set())):
                book.events[-1]['funding_price_source']='native_m15_mark_open_reference_not_api_settlement_quote'
        if settlements:
            risk(at)
        if at == config.end:
            last = {m:selected(prepared.closes, m, at-MS) for m in ('spot','perp')}
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
        is_signal_time = at in prepared.features['spot'][next(iter(config.weights))]
        if not is_signal_time:
            continue
        for market,side in (('spot',1),('perp',-1)):
            for s in sorted(active[market]):
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
                        book.event(at,'trailing_update',f'closed_{label}_atr14x3',market=market,symbol=s,
                            previous_stop=str(before),stop=str(trail.stop),signal_available_at=at.isoformat())
        # H4 closed immediately before this M15 open. Tightened stop is active
        # now, but cannot be used against the already closed M15/H4 candle.
        check_stops(book,at,opening,closed,opening=True)
        if closed:
            risk(at)
        if book.halted:
            flatten(book,at,opening,closed)
            risk(at)
        granted = (grant_rotation(at,opening,closed) if rotating else
                   grant_slots(at,opening,closed) if slots else None)
        for market,side in (('spot',1),('perp',-1)):
            candidates = granted[market] if granted is not None else collect(at,market,side,opening,closed)
            if market == 'spot' and candidates:
                if rotating:  # Best-ranked entry first; each takes its share of what the sleeve still has free.
                    for s,multiplier in candidates.items():
                        book.enter_batch(at,marks,{s:multiplier})
                else:
                    book.enter_batch(at,marks,candidates)
            for s,multiplier in candidates.items():
                f, price = prepared.features[market][s][at], opening[market][s].open
                if market == 'perp':
                    target = book.perp_budget(marks)*book.perp_weight(s)*multiplier
                    distance = stop*f['atr']/price
                    if hard_stop is not None:
                        distance = min(distance, hard_stop)
                    if not book.enter_perp(s,at,marks,price,-1,distance,approved_target=target):
                        continue
                    book.events[-1]['reason'] = 'donchian_short_entry'
                if position(book,market,s).quantity:
                    book.trails[(market,s)] = ATRTrail(side,price,f['atr'],stop,hard_stop)
                    book.event(at,'entry_features',f'closed_{label}',market=market,symbol=s,
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
        for s in sorted(market_weights(config,market)):
            trades = [t for t in book.trades if t['market']==market and t['symbol']==s]
            contributions[market+':'+s] = dict(closed_trades=len(trades), **{
                k:float(sum((t[k] for t in trades),ZERO)) for k in
                ('gross_pnl','exchange_fee','slippage_cost','funding_paid','net_pnl')})
    blockers = Counter(f for e in book.events if e['reason']=='indicator_filters' for f in e['failed_filters'])
    valid = not book.limitations
    symbols = sorted(set(config.weights) | set(market_weights(config,'perp')))
    payload = {**config.model_dump(mode='json'), 'symbol':'+'.join(s.removesuffix('USDT') for s in symbols),'market':'spot+short1x' if config.include_perp else 'spot'}
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
        summary['native_boundary_disclosure']=(five_policy.disclosure() if prepared.native_boundary_policy==five_policy.POLICY else
            join_disclosure() if prepared.native_boundary_policy==JOIN_POLICY else disclosure())
    if prepared.five_source_symbols:
        summary['new_coin_data_policy']=five_policy.disclosure()
    hourly = prepared.bar_interval == '1h'

    def timeframe(text):
        return text.replace('H4','H1') if hourly else text

    variant = {}
    if getattr(config, 'stop_atr_multiple', 3) != 3:
        variant['stop_atr_multiple'] = getattr(config, 'stop_atr_multiple')
    if config.exit_window == 40:
        variant['donchian_entry_exit'] = [config.entry_window, config.exit_window]
    if prepared.ema_periods != (50, 200):
        variant['ema_fast_slow'] = list(prepared.ema_periods)
    if prepared.adx_period != 14:
        variant['adx_dmi_period'] = prepared.adx_period
    if prepared.warmup_hours != 484:
        variant['h1_warmup_hours'] = prepared.warmup_hours
    if getattr(config, 'slots', None):
        variant['slots'] = dict(count=config.slots, ranker=getattr(config, 'ranker', 'adx_volume'),
                                seed=getattr(config, 'seed', None) if getattr(config, 'ranker', '') == 'random' else None)
    if getattr(config, 'universe', None) in ('baskets', 'rotation') and config.entry_window != 30:
        variant['donchian_entry_window'] = config.entry_window
    if getattr(config, 'max_positions', None):
        variant['rotation'] = dict(max_positions=config.max_positions, position_cap=config.position_cap,
            limit_scope=getattr(config, 'limit_scope', 'total'),
            slot_divisor=getattr(config, 'slot_divisor', None) or config.max_positions,
            rotation=config.rotation, rotation_margin=config.rotation_margin, pnl_gate=config.pnl_gate,
            hard_stop_pct=str(config.hard_stop_pct) if config.hard_stop_pct is not None else None)
    if prepared.regime_interval:
        variant['regime'] = ('EMA200/50 and ADX/DMI14 from the last closed H4 bar; Donchian, volume, profile, ATR '
                             'and stops stay on H1')

    return dict(schema_version='2',evaluator_version=VERSION,
        result_id=fingerprint(dict(version=VERSION,config=payload,data=prepared.checksum)),
        research_only=True,activation_allowed=False,official_gate_eligible=False,
        status='complete' if valid else 'limited',config=payload,
        inputs=dict(dataset_checksum=prepared.checksum,config_checksum=fingerprint(payload),funding_audit=prepared.funding_audit,
                    **({'signal_bar_derivation':prepared.derivation} if hourly else {})),
        summary=summary,methodology=dict(
            allocation=(f'two separate realized sleeves, Spot {config.entry_cap*100}% and Perp {config.perp_cap*100}%; no per-coin '
                        f'weights; at most {config.max_positions} open positions '+
                        ('across both sleeves, one per coin; ' if getattr(config,'limit_scope','total') == 'total' else
                         'in each sleeve, one per coin in that sleeve (a coin may hold both markets); ')+
                        'a position uses '+('the free sleeve' if config.position_cap == 'none' else
                                            '1/'+str(getattr(config,'slot_divisor',None) or config.max_positions)+' of its sleeve')+
                        ' times the ATR multiplier; entries ranked by ADX then volume ratio'
                        if getattr(config,'max_positions',None) else
                        f'{config.slots} shared slots of one realized pool ({len(config.weights)} coins); Spot long or Perp short1x, '
                        f'one position per coin; entries ranked by {getattr(config,"ranker","adx_volume")}'
                        if getattr(config,'slots',None) else
                        allocation_text(config)+'; Short-only1x; independent realized Spot/Perp sizing, no reserve/transfers'
                        if hasattr(config,'adx_by_market') else
                        '60/40 Spot/isolated Short1x; both '+ ' '.join(s.removesuffix('USDT')+str(int(w*100)) for s,w in config.weights.items())+'; independent realized sizing, no reserve/transfers'),
            signals=timeframe('closed native H4 Donchian; preceding channel excludes trigger; no D1/Jev/LLM'),
            atr='simple TR14 preserves old sizing min(1,.02/ATR%); initial and ratcheting price stop ATRx3',
            indicators='EMA SMA seeds; DMI14/ADX14 Wilder; prior20 volume SMA and strict >1.2x',
            profile=timeframe('prior120 H4 excluding trigger; native M15 uniform volume in low/high,50 bins,VA70%,POC tie lower/expansion tie upper'),
            trailing=timeframe('closed H4 high/low since entry; new stop effective next M15; old stop evaluated first'),
            daily='combined marked start-of-UTC-day equity3%; pending flatten next M15 open; next UTC day AND flat resume',
            fills=timeframe('H4 signal next H4 open; old stops gap at open or touch priced at stop/logged at M15 close; no same-bar reentry'),
            costs=('Spot10/5bps, Perp5/5bps fee/cash-charged slippage each fill; actual funding separate'
                   if getattr(config, 'cost_multiplier', 1) == 1 else
                   'Spot20/10bps, Perp10/10bps fee/cash-charged slippage each fill; actual funding unchanged'),
            drawdown='native M15 marks open/close plus funding/cost; observe-only; not exact tick/intrabar DD',
            **({'variant': variant} if variant else {}),
            **({'funding_pricing':'Original API rates/times; missing settlement quotes use actual native mark open 0-31ms before settlement, explicitly reference not API-confirmed settlement price'}
               if prepared.funding_reference_times else {})),
        limitations=sorted(book.limitations | {'approximate_m15_volume_profile_not_tick_volume_at_price',
            'm15_ohlc_touch_execution_order_unknown', 'daily_gaps_and_exit_costs_can_overshoot',
            'ideal_fractional_fills_without_order_book_or_partial_fills','no_exact_liquidation_or_historical_demo_simulation',
            'window_already_seen_not_untouched_out_of_sample'} | (
                {'hourly_bars_derived_from_native_m15_not_native_h1'} if hourly else set()) | (
                {'spot_source_gap_stale_valuation_and_delayed_execution'} if prepared.spot_gap_policy else set()) | (
                {'mark_source_gap_stale_valuation'} if prepared.mark_gap_policy else set()) | (
                {'missing_funding_settlement_quote_native_mark_open_reference_0_to_31ms'} if prepared.funding_reference_times else set()) | (
                {'exact_whitelisted_native_h4_m15_price_disagreements'} if prepared.native_boundary_policy else set())),
        v1_reference=None,equity_curve=book.curve,trades=book.trades,events=book.events)
