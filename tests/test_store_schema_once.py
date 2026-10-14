"""Schema setup runs once per database file in a process and retries a briefly locked database."""
import sqlite3
import threading

import pytest

from intraday import store as store_module
from intraday.store import IntradayStore


def test_schema_setup_runs_once_per_file_and_again_for_a_replaced_file(tmp_path, monkeypatch):
    calls = []
    original = IntradayStore._initialize
    monkeypatch.setattr(IntradayStore, '_initialize', lambda self: (calls.append(self.database), original(self))[1])
    path = tmp_path/'intraday.sqlite'
    IntradayStore(path)
    IntradayStore(path)
    threads = [threading.Thread(target=IntradayStore, args=(path,)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
    path.unlink()                       # a restored or replaced file gets its schema again
    for suffix in ('-wal', '-shm'):
        (tmp_path/('intraday.sqlite'+suffix)).unlink(missing_ok=True)
    IntradayStore(path)
    assert len(calls) == 2
    IntradayStore(tmp_path/'other.sqlite')
    assert len(calls) == 3


def test_a_locked_database_is_retried_and_other_errors_are_not(tmp_path, monkeypatch):
    original = IntradayStore._initialize
    failures = {'n': 2}

    def flaky(self):
        if failures['n']:
            failures['n'] -= 1
            raise sqlite3.OperationalError('database is locked')
        return original(self)
    monkeypatch.setattr(IntradayStore, '_initialize', flaky)
    monkeypatch.setattr(store_module.time, 'sleep', lambda seconds: None)
    IntradayStore(tmp_path/'a.sqlite')
    assert failures['n'] == 0
    monkeypatch.setattr(IntradayStore, '_initialize',
                        lambda self: (_ for _ in ()).throw(sqlite3.OperationalError('no such table: x')))
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        IntradayStore(tmp_path/'b.sqlite')
