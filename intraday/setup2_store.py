"""Setup-2 bar storage, public-data sync and soak observations (ADR-006 stage B).

Extension tables only; the source schema version is unchanged. Bars are immutable once stored,
and each evaluated closed H4 bar leaves one observation row, which is the Setup-2 heartbeat.
"""

from datetime import datetime, timezone
import json
import sqlite3

from intraday import active_set, setup2
from intraday.contracts import DecisionScope

EXTENSION_VERSION = '1'
WIDTHS = {'4h': setup2.H4, '15m': setup2.M15}
SCOPES = {'spot': DecisionScope.SPOT_4H}  # perp_4h joins in stage C.


def installed(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='setup2_meta'").fetchone():
        return False
    row = connection.execute("SELECT value FROM setup2_meta WHERE key='version'").fetchone()
    if row is None or row['value'] != EXTENSION_VERSION:
        raise ValueError('unsupported setup2 extension')
    return True


def install(store):
    if store.read_only:
        raise ValueError('setup2 storage requires explicit writer')
    with store._connect() as c:
        installed(c)
        c.executescript('''
            CREATE TABLE IF NOT EXISTS setup2_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO setup2_meta VALUES ('version','1');
            CREATE TABLE IF NOT EXISTS setup2_candles (
                symbol TEXT NOT NULL, market TEXT NOT NULL CHECK(market IN ('spot','perp')),
                interval TEXT NOT NULL CHECK(interval IN ('15m','4h')), open_time INTEGER NOT NULL,
                close_time INTEGER NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY(symbol, market, interval, open_time)
            );
            CREATE TABLE IF NOT EXISTS setup2_observations (
                symbol TEXT NOT NULL, market TEXT NOT NULL CHECK(market IN ('spot','perp')),
                bar_close TEXT NOT NULL, rule_id TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
                jev_status TEXT NOT NULL CHECK(jev_status IN
                    ('called','skipped_no_setup','skipped_inactive','provider_error','gate_error')),
                observation_json TEXT NOT NULL, evaluator_version TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(symbol, market, bar_close, rule_id)
            );
        ''')


def record_candles(store, symbol, market, interval, rows):
    """Append closed bars; a different payload for a stored bar is a hard error."""
    install(store)
    width = int(WIDTHS[interval].total_seconds()*1000)
    with store._connect() as c:
        for row in rows:
            opening, closing = int(row[0]), int(row[6])
            if opening % width or closing != opening+width-1:
                raise ValueError('setup2 candle does not match its interval')
            payload = json.dumps(list(row), separators=(',', ':'))
            stored = c.execute('SELECT payload_json FROM setup2_candles WHERE symbol=? AND market=? AND interval=? '
                               'AND open_time=?', (symbol, market, interval, opening)).fetchone()
            if stored is None:
                c.execute('INSERT INTO setup2_candles VALUES (?,?,?,?,?,?)',
                          (symbol, market, interval, opening, closing, payload))
            elif stored['payload_json'] != payload:
                raise ValueError(f'stored {market} {interval} bar changed for {symbol} at {opening}')


def list_bars(store, symbol, market, interval, *, since=None, until=None):
    with store._connect() as c:
        if not installed(c):
            return []
        rows = c.execute('SELECT payload_json FROM setup2_candles WHERE symbol=? AND market=? AND interval=? '
                         'AND open_time>=? AND close_time<? ORDER BY open_time',
                         (symbol, market, interval, int(since.timestamp()*1000) if since else 0,
                          int(until.timestamp()*1000) if until else 2**62)).fetchall()
    return setup2.bars([json.loads(r['payload_json']) for r in rows], WIDTHS[interval])


def profile_start(anchor):
    """First M15 bar needed: the profile of the first fully warmed-up trigger bar."""
    return anchor+(setup2.WARMUP_BARS-1)*setup2.H4-setup2.PROFILE_WINDOW


def sync(store, symbol, market, *, anchor, now, fetch_spot=None, fetch_perp=None):
    """Fetch closed public bars from the last stored one (or the anchor) to the H4 boundary."""
    from intraday.replay_v2.donchian_filter_data import fetch_spot_snapshot
    from intraday.replay_v2.historical_data import fetch_candle_snapshot
    end = setup2.floor_h4(now)
    fetched = {}
    for interval, first in (('4h', anchor), ('15m', profile_start(anchor))):
        stored = list_bars(store, symbol, market, interval, since=first)
        start = stored[-1].available_at if stored else first
        if start >= end:
            fetched[interval] = 0
            continue
        if market == 'spot':
            snapshot = (fetch_spot or fetch_spot_snapshot)(symbol, interval, start, end, now=now)
        else:
            snapshot = (fetch_perp or fetch_candle_snapshot)(symbol, interval, start, end, price_kind='trade', now=now)
        record_candles(store, symbol, market, interval, snapshot.raw_rows)
        fetched[interval] = len(snapshot.raw_rows)
    return fetched


def evaluate(store, symbol, market, *, anchor, now):
    side = 1 if market == 'spot' else -1
    until = setup2.floor_h4(now)
    return setup2.evaluate_setup2(list_bars(store, symbol, market, '4h', since=anchor, until=until),
                                  list_bars(store, symbol, market, '15m', since=profile_start(anchor), until=until),
                                  side=side, anchor=anchor)


def record_observation(store, symbol, market, rule_id, observation, *, active, jev_status, now):
    install(store)
    with store._connect() as c:
        c.execute('INSERT OR IGNORE INTO setup2_observations VALUES (?,?,?,?,?,?,?,?,?)',
                  (symbol, market, observation.bar_close.isoformat() if observation.bar_close else now.isoformat(),
                   rule_id, int(active), jev_status, json.dumps(observation.payload(), sort_keys=True),
                   observation.evaluator_version, now.isoformat()))


def observations(store, symbol, market, *, rule_id=None, since=None, until=None):
    with store._connect() as c:
        if not installed(c):
            return []
        rows = c.execute('SELECT * FROM setup2_observations WHERE symbol=? AND market=? ORDER BY bar_close',
                         (symbol, market)).fetchall()
    result = [dict(r) | {'observation': json.loads(r['observation_json'])} for r in rows]
    return [r for r in result if (rule_id is None or r['rule_id'] == rule_id)
            and (since is None or r['bar_close'] >= since.isoformat())
            and (until is None or r['bar_close'] <= until.isoformat())]


def heartbeat(store, symbol, market, rule_id, *, start, end):
    """Distinct evaluated H4 bars over expected bars; the Setup-2 soak heartbeat."""
    seen = {r['bar_close'] for r in observations(store, symbol, market, rule_id=rule_id, since=start, until=end)
            if r['jev_status'] not in ('provider_error', 'gate_error')}
    expected = max(1, int((setup2.floor_h4(end)-setup2.floor_h4(start))/setup2.H4))
    return min(1.0, len(seen)/expected)


def coin_anchor(store, symbol):
    version = active_set.current(store)
    anchor = version.plan.anchor(symbol) if version else None
    if anchor is None:
        raise ValueError('setup2 rules run only for active or watched coins')
    return anchor


def run_setup2_observation(store, provider, snapshot, *, rule, market, now):
    """Evaluate the closed bar; call Jev only for an active coin whose full setup fired."""
    from intraday.journal import _fallback_trace, record_scoped_signal
    from intraday.scoped_rule_lifecycle import rule_allows_answers
    scope = SCOPES[market]
    symbol = snapshot.symbol
    anchor = coin_anchor(store, symbol)
    observation = evaluate(store, symbol, market, anchor=anchor, now=now)
    active = active_set.is_active(store, symbol, scope) is True
    if not observation.entry or not active:
        status = 'skipped_no_setup' if active else 'skipped_inactive'
        record_observation(store, symbol, market, rule.rule_id, observation, active=active, jev_status=status, now=now)
        store.record_portfolio_soak_tick(symbol=symbol, scope=scope, status='skipped_no_setup', created_at=now)
        return status
    tick_id = f"{symbol}:{scope.value}:setup2:{int(observation.bar_close.timestamp()*1000)}"
    try:
        scoped = provider.decide_scoped(snapshot, tick_id, scope, now)
        signal_id = record_scoped_signal(store, snapshot, scoped, gate_passed=False,
                                         gate_reason='setup2_soak_observation_only', rule_id=rule.rule_id)
        answers = (scoped.trace or _fallback_trace(snapshot, scoped)).jev_answers
        challenger = store.load_scoped_challenger(scope, symbol=symbol)
        if challenger and challenger.rule_id == rule.rule_id:
            store.record_scoped_rule_soak_tick(
                candidate_id=rule.rule_id, signal_id=signal_id, champion_allowed=False,
                challenger_allowed=rule_allows_answers(rule, answers), champion_score=0, challenger_score=0,
                created_at=now)
    except RuntimeError:
        status, jev = 'provider_error', 'provider_error'
    except (ValueError, sqlite3.Error):
        status, jev = 'gate_error', 'gate_error'
    else:
        status, jev = 'success', 'called'
    record_observation(store, symbol, market, rule.rule_id, observation, active=True, jev_status=jev, now=now)
    store.record_portfolio_soak_tick(symbol=symbol, scope=scope, status=status, created_at=now)
    return status
