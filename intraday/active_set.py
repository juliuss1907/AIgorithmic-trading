"""Audited operator active set (ADR-004): at most three coins, per-coin mode and weights.

No version recorded means legacy behaviour everywhere, so deploying this module changes nothing.
Once a version exists, only its coins' rule scopes may cross the model boundary; every other
coin keeps collecting market data, observation only.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from intraday.assets import normalize_symbol
from intraday.contracts import DecisionScope

EXTENSION_VERSION = '1'
MAX_COINS = 3
# Rule scopes per market; perp_4h arrives with the rule-driven Perp short (ADR-006 stage C).
RULE_SCOPES = {'spot': 'spot_4h', 'perp': 'perp_4h'}
H4 = timedelta(hours=4)
WARMUP_BARS = 600


class CoinPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    mode: Literal['spot', 'perp', 'both']
    spot_weight: Decimal = Field(ge=0, le=1)
    perp_weight: Decimal = Field(ge=0, le=1)
    indicator_anchor: datetime

    @property
    def markets(self):
        return {'spot', 'perp'} if self.mode == 'both' else {self.mode}

    @model_validator(mode='after')
    def weights_follow_mode(self):
        for market in ('spot', 'perp'):
            if (getattr(self, market+'_weight') > 0) != (market in self.markets):
                raise ValueError('a market has a positive weight exactly when the mode enables it')
        if self.indicator_anchor.tzinfo is None or self.indicator_anchor.utcoffset() != timedelta(0):
            raise ValueError('indicator anchor must be UTC')
        if (self.indicator_anchor - datetime(1970, 1, 1, tzinfo=timezone.utc)) % H4:
            raise ValueError('indicator anchor must be an H4 boundary')
        return self


class ActiveSetPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    coins: dict[str, CoinPlan]
    spot_split: Decimal = Field(default=Decimal('0.60'), ge=0, le=1)
    perp_split: Decimal = Field(default=Decimal('0.40'), ge=0, le=1)
    perp_leverage: Literal[1] = 1
    rule_profile: Literal['setup2_v1'] = 'setup2_v1'
    perp_intraday_enabled: Literal[False] = False
    # Rotation candidates (ADR-004): rule-only Setup-2 soak, no Jev and no trading.
    watch: dict[str, datetime] = Field(default_factory=dict)

    @field_validator('watch')
    @classmethod
    def canonical_watch(cls, watch):
        if len(watch) > MAX_COINS or any(normalize_symbol(s) != s for s in watch):
            raise ValueError(f'watch at most {MAX_COINS} canonical USDT symbols')
        for anchor in watch.values():
            if anchor.tzinfo is None or anchor.utcoffset() != timedelta(0) or (
                    anchor-datetime(1970, 1, 1, tzinfo=timezone.utc)) % H4:
                raise ValueError('watch anchors must be UTC H4 boundaries')
        return dict(sorted(watch.items()))

    @field_validator('coins')
    @classmethod
    def canonical_coins(cls, coins):
        if not 1 <= len(coins) <= MAX_COINS:
            raise ValueError(f'the active set holds one to {MAX_COINS} coins')
        if any(normalize_symbol(s) != s for s in coins):
            raise ValueError('active set requires canonical USDT symbols')
        return dict(sorted(coins.items()))

    @model_validator(mode='after')
    def budgets(self):
        if self.spot_split + self.perp_split > 1:
            raise ValueError('Spot and Perp splits must total at most one')
        for market in ('spot', 'perp'):
            total = sum((getattr(c, market+'_weight') for c in self.coins.values()), Decimal(0))
            if total > 1:
                raise ValueError(f'{market} weights must total at most one')
            if total > 0 and getattr(self, market+'_split') <= 0:
                raise ValueError(f'{market} coins need a positive {market} split')
        if set(self.watch) & set(self.coins):
            raise ValueError('a coin is either active or watched, not both')
        return self

    def anchor(self, symbol):
        coin = self.coins.get(symbol)
        return coin.indicator_anchor if coin else self.watch.get(symbol)

    def scopes(self, symbol):
        coin = self.coins.get(symbol)
        return {RULE_SCOPES[m] for m in coin.markets} if coin else set()


class ActiveSetVersion(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    version_id: str
    seq: int
    previous_version_id: str | None
    created_at: datetime
    actor: str
    reason: str
    plan: ActiveSetPlan


def equal_plan(symbols, *, mode='both', anchor, spot_split='0.60', perp_split='0.40'):
    """Equal weights in each enabled market, exact at Decimal precision (ADR-005 basket)."""
    from intraday.replay_v2.donchian_adx_setups import weights
    thirds = weights(**{normalize_symbol(s).removesuffix('USDT'): 1 for s in symbols})
    zero = Decimal(0)
    coins = {s: CoinPlan(mode=mode, spot_weight=w if mode in ('spot', 'both') else zero,
                         perp_weight=w if mode in ('perp', 'both') else zero, indicator_anchor=anchor)
             for s, w in thirds.items()}
    return ActiveSetPlan(coins=coins, spot_split=Decimal(spot_split), perp_split=Decimal(perp_split))


def installed(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_set_meta'").fetchone():
        return False
    row = connection.execute("SELECT value FROM active_set_meta WHERE key='version'").fetchone()
    if row is None or row['value'] != EXTENSION_VERSION:
        raise ValueError('unsupported active set extension')
    return True


def install(store):
    if store.read_only:
        raise ValueError('active set requires explicit writer')
    with store._connect() as c:
        installed(c)
        c.executescript('''
            CREATE TABLE IF NOT EXISTS active_set_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO active_set_meta VALUES ('version','1');
            CREATE TABLE IF NOT EXISTS active_set_versions (
                version_id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, previous_version_id TEXT,
                created_at TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS active_set_versions_no_update BEFORE UPDATE ON active_set_versions
                BEGIN SELECT RAISE(ABORT, 'active set versions are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS active_set_versions_no_delete BEFORE DELETE ON active_set_versions
                BEGIN SELECT RAISE(ABORT, 'active set versions are append-only'); END;
            CREATE TABLE IF NOT EXISTS active_set_current (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), version_id TEXT NOT NULL
                    REFERENCES active_set_versions(version_id), updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS active_set_observations (
                symbol TEXT NOT NULL, scope TEXT NOT NULL, slot TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status='skipped_inactive'), created_at TEXT NOT NULL,
                PRIMARY KEY(symbol, scope, slot)
            );
        ''')


def _version(row):
    return ActiveSetVersion(version_id=row['version_id'], seq=row['seq'], previous_version_id=row['previous_version_id'],
                            created_at=datetime.fromisoformat(row['created_at']), actor=row['actor'],
                            reason=row['reason'], plan=ActiveSetPlan.model_validate_json(row['payload_json']))


def current(store):
    with store._connect() as c:
        if not installed(c):
            return None
        row = c.execute('SELECT v.* FROM active_set_current k JOIN active_set_versions v USING(version_id) '
                        'WHERE k.singleton=1').fetchone()
    return _version(row) if row else None


def history(store):
    with store._connect() as c:
        if not installed(c):
            return []
        rows = c.execute('SELECT * FROM active_set_versions ORDER BY seq').fetchall()
    return [_version(r) for r in rows]


def is_active(store, symbol, scope, *, version=None):
    """None: no active set yet (legacy). Otherwise whether this coin/scope may call Jev."""
    version = version or current(store)
    if version is None:
        return None
    value = scope.value if isinstance(scope, DecisionScope) else scope
    return value in version.plan.scopes(normalize_symbol(symbol))


def allows(store, symbol, scope):
    return is_active(store, symbol, scope) is not False


def watched(store, symbol, *, version=None):
    version = version or current(store)
    return bool(version and normalize_symbol(symbol) in version.plan.watch)


def record_skip(store, symbol, scope, *, now):
    """Audit row per coin, scope and H4 slot proving inactive ticks skipped the model boundary."""
    if store.read_only:
        raise ValueError('active set requires explicit writer')
    value = scope.value if isinstance(scope, DecisionScope) else scope
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    slot = epoch + (now - epoch)//H4*H4
    with store._connect() as c:
        c.execute('INSERT OR IGNORE INTO active_set_observations VALUES (?,?,?,?,?)',
                  (normalize_symbol(symbol), value, slot.isoformat(), 'skipped_inactive', now.isoformat()))
    return 'skipped_inactive'


def history_bars(store, symbol, anchor, now):
    """Contiguous closed Spot H4 bars from the anchor; a gap stops the count."""
    rows = store.list_asset_candles(symbol, '4h', as_of=now)
    expected, count = int(anchor.timestamp()*1000), 0
    for row in rows:
        if int(row[0]) < expected:
            continue
        if int(row[0]) != expected:
            break
        count, expected = count+1, expected + int(H4.total_seconds()*1000)
    return count


def setup2_soak_passed(store, symbol, market):
    """A passing v2 soak evaluation for a Setup-2 rule of this coin and market."""
    from intraday.replay_v2.gate_repository import GateRepository
    repository = GateRepository(store)
    scope = DecisionScope.SPOT_4H if market == 'spot' else DecisionScope.PERP_INTRADAY
    for row in store.list_scoped_rules(scope, symbol=symbol):
        rule = store.load_scoped_rule(row['id'])
        if getattr(rule.parameters, 'entry_profile', None) == 'setup2_v1':
            latest = repository.latest(rule.rule_id, kind='soak')
            if latest is not None and latest.status == 'pass':
                return True
    return False


def rotation_blockers(store, symbol, coin, now):
    """Fast rotation (ADR-004): history and a passing Setup-2 soak per market; no backtest."""
    blockers = []
    if 'spot' in coin.markets and history_bars(store, symbol, coin.indicator_anchor, now) < WARMUP_BARS:
        blockers.append('history_not_ready')
    blockers += [f'{m}_setup2_gate_required' for m in sorted(coin.markets) if not setup2_soak_passed(store, symbol, m)]
    return blockers


def switch(store, plan, *, actor, reason, now, execution_check, initial=False):
    """Append and select a new version; only when paused and flat (ADR-004)."""
    if not actor.strip() or not reason.strip():
        raise ValueError('active set changes require an actor and a reason')
    catalog = store.asset_catalog()
    if missing := sorted(set(plan.coins) - set(catalog)):
        raise ValueError('coins must be registered in the catalog first: '+', '.join(missing))
    previous = current(store)
    if initial and previous is not None:
        raise ValueError('the active set is already initialized; use switch')
    if not initial and previous is None:
        raise ValueError('initialize the active set first')
    execution_check()
    state = store.load_parent_portfolio_state()
    if state is not None and (state.spot_quantity or state.perp_quantity or not state.entries_paused):
        raise ValueError('pause and flatten the BTC parent paper portfolio first')
    if not initial:
        added = {s: c for s, c in plan.coins.items()
                 if s not in previous.plan.coins or c.markets - previous.plan.coins[s].markets}
        blocked = {s: rotation_blockers(store, s, c, now) for s, c in added.items()}
        if blocked := {s: b for s, b in blocked.items() if b}:
            raise ValueError('coins are not ready to rotate in: '+json.dumps(blocked, sort_keys=True))
    payload = plan.model_dump_json()
    version_id = hashlib.sha256(f"{previous.version_id if previous else ''}:{payload}".encode()).hexdigest()
    install(store)
    with store._connect() as c:
        seq = (c.execute('SELECT MAX(seq) FROM active_set_versions').fetchone()[0] or 0) + 1
        c.execute('INSERT INTO active_set_versions VALUES (?,?,?,?,?,?,?)',
                  (version_id, seq, previous.version_id if previous else None, now.isoformat(),
                   actor.strip(), reason.strip(), payload))
        c.execute('INSERT INTO active_set_current VALUES (1,?,?) ON CONFLICT(singleton) '
                  'DO UPDATE SET version_id=excluded.version_id, updated_at=excluded.updated_at',
                  (version_id, now.isoformat()))
    return current(store)


def readiness_view(store, symbol, market, now, *, version=None):
    """Active-set membership plus fast-rotation labels for one coin/market (ADR-004)."""
    from intraday.replay_v2.donchian_adx_setups import CANDIDATES
    version = version or current(store)
    coin = version.plan.coins.get(symbol) if version else None
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    anchor = coin.indicator_anchor if coin else epoch + (now - epoch)//H4*H4 - WARMUP_BARS*H4
    labels = []
    if market == 'spot' and history_bars(store, symbol, anchor, now) < WARMUP_BARS:
        labels.append('history_not_ready')
    if symbol.removesuffix('USDT') not in CANDIDATES:
        labels.append('no_backtest_evidence')
    return {'version_id': version.version_id if version else None,
            'member': None if version is None else coin is not None,
            'market_enabled': None if version is None else bool(coin and market in coin.markets),
            'mode': coin.mode if coin else None,
            'weight': str(getattr(coin, market+'_weight')) if coin else None,
            'indicator_anchor': anchor.isoformat(), 'rotation_labels': labels}


def describe(version):
    if version is None:
        return {'active_set': None, 'mode': 'legacy'}
    return {'version_id': version.version_id, 'seq': version.seq, 'created_at': version.created_at.isoformat(),
            'actor': version.actor, 'reason': version.reason, **json.loads(version.plan.model_dump_json())}
