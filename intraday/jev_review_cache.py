"""Dedicated append-only research opinions. Never opens the intraday journal."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from intraday.setup2_review import MODEL, QUESTION_SET_VERSION, STATE_VERSION, parse_answer

DEFAULT_CACHE = Path.home()/'.local/state/aigorithmic-trading/jev-review/answers.sqlite3'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def answer_key(state, *, model=MODEL, question_version=QUESTION_SET_VERSION, state_version=STATE_VERSION):
    return digest(dict(model=model, question_set_version=question_version, state_version=state_version,
                       state=list(state)))


class AnswerCache:
    def __init__(self, path=DEFAULT_CACHE, *, writable=False):
        self.path, self.writable = Path(path).expanduser().resolve(), writable
        self.connection = None
        if writable:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.connection = sqlite3.connect(self.path)
            self.connection.executescript('''
                CREATE TABLE IF NOT EXISTS answers (
                    key TEXT PRIMARY KEY, model TEXT NOT NULL, question_version TEXT NOT NULL,
                    state_version TEXT NOT NULL, state_json TEXT NOT NULL, response_json TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL, returned_model TEXT NOT NULL, collected_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    key TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
                    workflow TEXT NOT NULL, recorded_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS answers_no_update BEFORE UPDATE ON answers
                    BEGIN SELECT RAISE(ABORT, 'append-only answers'); END;
                CREATE TRIGGER IF NOT EXISTS answers_no_delete BEFORE DELETE ON answers
                    BEGIN SELECT RAISE(ABORT, 'append-only answers'); END;
                CREATE TRIGGER IF NOT EXISTS attempts_no_update BEFORE UPDATE ON attempts
                    BEGIN SELECT RAISE(ABORT, 'append-only attempts'); END;
                CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON attempts
                    BEGIN SELECT RAISE(ABORT, 'append-only attempts'); END;
            ''')
        elif self.path.exists():
            self.connection = sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True)
        if self.connection:
            self.connection.row_factory = sqlite3.Row

    def __enter__(self):
        # One read snapshot binds the entire overlay even if a collector appends concurrently.
        if self.connection and not self.writable:
            self.connection.execute('BEGIN')
        return self

    def __exit__(self, *args):
        if self.connection:
            self.connection.close()

    def _row(self, state, model):
        if not self.connection:
            return None
        row = self.connection.execute('SELECT * FROM answers WHERE key=?', (answer_key(state, model=model),)).fetchone()
        if row is not None:
            payload = json.loads(row['response_json'])
            if digest(payload) != row['response_sha256'] or row['state_json'] != canonical(list(state)) or (
                row['model'], row['question_version'], row['state_version']) != (model, QUESTION_SET_VERSION, STATE_VERSION):
                raise ValueError('cache checksum or identity mismatch')
            answer = parse_answer(payload)
            if answer is None or answer.model != row['returned_model']:
                raise ValueError('invalid immutable cached answer')
        return row

    def get(self, state, *, model=MODEL):
        row = self._row(state, model)
        return parse_answer(json.loads(row['response_json'])) if row is not None else None

    def _writer(self):
        if not self.writable:
            raise ValueError('cache is read-only')

    def put(self, state, payload, *, model=MODEL):
        self._writer()
        answer = parse_answer(payload)
        if answer is None:
            raise ValueError('invalid review answer cannot be cached')
        row = self._row(state, model)
        checksum = digest(payload)
        if row is not None:
            if row['response_sha256'] != checksum:
                raise ValueError('immutable answer already exists')
            return False
        with self.connection:
            self.connection.execute('INSERT INTO answers VALUES (?,?,?,?,?,?,?,?,?)',
                (answer_key(state, model=model), model, QUESTION_SET_VERSION, STATE_VERSION,
                 canonical(list(state)), canonical(payload), checksum, answer.model,
                 datetime.now(timezone.utc).isoformat()))
        return True

    def record_attempt(self, state, status, *, attempts=1, model=MODEL):
        self._writer()
        if status not in {'success', 'timeout', 'network_error', 'rate_limited', 'provider_unavailable',
                          'invalid_response', 'model_mismatch', 'auth_failed', 'access_denied',
                          'insufficient_credits', 'http_error'} or not 1 <= attempts <= 10:
            raise ValueError('invalid redacted collection status')
        with self.connection:
            self.connection.execute('INSERT INTO attempts VALUES (?,?,?,?,?)',
                (answer_key(state, model=model), status, attempts, 'setup2_review', datetime.now(timezone.utc).isoformat()))

    def used_digest(self, states, *, model=MODEL):
        """Bind only the answers used, including explicit missing keys; unrelated appends do not matter."""
        used = {}
        for state in states:
            row = self._row(state, model)
            used[answer_key(state, model=model)] = row['response_sha256'] if row is not None else None
        return digest(dict(model=model, state_version=STATE_VERSION, question_set_version=QUESTION_SET_VERSION,
                           answers=sorted(used.items())))
