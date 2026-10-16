"""Append-only paid-call reservations, shared across retries and collection resumes."""

from decimal import Decimal, InvalidOperation, ROUND_CEILING
import uuid

NANO = Decimal(1_000_000_000)
INPUT_PRICE = Decimal('.042') / 1_000_000
MAX_INPUT_TOKENS = 262144
RESERVATION = int((MAX_INPUT_TOKENS * INPUT_PRICE * NANO).to_integral_value(rounding=ROUND_CEILING))


def response_cost(payload):
    """Only billing metadata is inspected; invalid/missing usage keeps the reservation."""
    usage = payload.get('usage') if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    cost = usage.get('cost')
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        try:
            value = Decimal(str(cost))
            if value.is_finite() and value >= 0:
                return value
        except InvalidOperation:
            pass
    tokens = usage.get('prompt_tokens', usage.get('input_tokens'))
    if isinstance(tokens, int) and not isinstance(tokens, bool) and 0 <= tokens <= MAX_INPUT_TOKENS:
        return tokens * INPUT_PRICE
    return None


class CollectionBudget:
    def __init__(self, cache, maximum=Decimal('1')):
        maximum = Decimal(str(maximum))
        if not maximum.is_finite() or not 0 < maximum <= 1:
            raise ValueError('collection budget must be positive and at most one USD')
        cache._writer()
        self.db, self.limit = cache.connection, int(maximum*NANO)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS cost_reservations (id TEXT PRIMARY KEY, nano INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS cost_settlements (id TEXT PRIMARY KEY, nano INTEGER NOT NULL,
                FOREIGN KEY(id) REFERENCES cost_reservations(id));
            CREATE TRIGGER IF NOT EXISTS cost_reservations_no_update BEFORE UPDATE ON cost_reservations
                BEGIN SELECT RAISE(ABORT, 'append-only cost ledger'); END;
            CREATE TRIGGER IF NOT EXISTS cost_reservations_no_delete BEFORE DELETE ON cost_reservations
                BEGIN SELECT RAISE(ABORT, 'append-only cost ledger'); END;
            CREATE TRIGGER IF NOT EXISTS cost_settlements_no_update BEFORE UPDATE ON cost_settlements
                BEGIN SELECT RAISE(ABORT, 'append-only cost ledger'); END;
            CREATE TRIGGER IF NOT EXISTS cost_settlements_no_delete BEFORE DELETE ON cost_settlements
                BEGIN SELECT RAISE(ABORT, 'append-only cost ledger'); END;
        ''')

    def _total(self):
        return self.db.execute('''SELECT COALESCE(SUM(COALESCE(s.nano,r.nano)),0)
            FROM cost_reservations r LEFT JOIN cost_settlements s ON r.id=s.id''').fetchone()[0]

    def reserve(self):
        # Serialize concurrent collectors. A crash leaves a charged reservation, never free calls.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self._total()+RESERVATION > self.limit:
                self.db.rollback()
                return None
            identifier = uuid.uuid4().hex
            self.db.execute('INSERT INTO cost_reservations VALUES (?,?)', (identifier, RESERVATION))
            self.db.commit()
            return identifier
        except BaseException:
            self.db.rollback()
            raise

    def settle(self, identifier, cost):
        if cost is None:
            return
        nano = int((cost*NANO).to_integral_value(rounding=ROUND_CEILING))
        with self.db:
            self.db.execute('INSERT INTO cost_settlements VALUES (?,?)', (identifier, nano))
        if nano > RESERVATION or self._total() > self.limit:
            raise ValueError('provider reported cost exceeds reserved budget; stop collection')

    def payload(self):
        settled = self.db.execute('SELECT COALESCE(SUM(nano),0) FROM cost_settlements').fetchone()[0]
        total = self._total()
        return dict(limit_usd=str(Decimal(self.limit)/NANO), accounted_usd=str(Decimal(total)/NANO),
                    reported_or_token_cost_usd=str(Decimal(settled)/NANO),
                    unresolved_reservations_usd=str(Decimal(total-settled)/NANO),
                    per_call_reservation_usd=str(Decimal(RESERVATION)/NANO),
                    reserved_calls=self.db.execute('SELECT COUNT(*) FROM cost_reservations').fetchone()[0])
