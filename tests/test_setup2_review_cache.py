import json
import sqlite3

import pytest

from intraday.jev_review_cache import AnswerCache, answer_key
from intraday.setup2_review import MODEL
from test_setup2_review import payload

STATE = ['spot_long', 'breakout_twenty_five']


def test_key_is_versioned_and_ordered():
    key = answer_key(STATE)
    assert len(key) == 64 and key == answer_key(STATE)
    assert key != answer_key(STATE, model='other-model')
    assert key != answer_key(STATE, state_version='next-state')
    assert key != answer_key(STATE, question_version='next-question')
    assert key != answer_key(STATE[::-1])


def test_missing_read_only_cache_does_not_create_anything(tmp_path):
    path = tmp_path/'absent'/'answers.sqlite3'
    with AnswerCache(path) as cache:
        assert cache.get(STATE) is None
        assert cache.used_digest([STATE]) == cache.used_digest([STATE])
    assert not path.parent.exists()


def test_append_only_answer_survives_restart_and_freezes_used_subset(tmp_path):
    path = tmp_path/'answers.sqlite3'
    with AnswerCache(path, writable=True) as cache:
        assert cache.put(STATE, payload())
        before = cache.used_digest([STATE])
        assert not cache.put(STATE, payload())
        cache.put(['perp_short'], payload())
        assert cache.used_digest([STATE]) == before
        changed = payload()
        changed['answers']['exhaustion']['noul'] = .9
        with pytest.raises(ValueError, match='immutable'):
            cache.put(STATE, changed)
        cache.record_attempt(STATE, 'timeout', attempts=2)
    with AnswerCache(path) as cache:
        assert cache.get(STATE).model == payload()['model']
        assert cache.used_digest([STATE]) == before
        with pytest.raises(ValueError, match='read-only'):
            cache.put(STATE, payload())
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM attempts').fetchone()[0] == 1
        for sql in ('UPDATE answers SET model="other"', 'DELETE FROM answers', 'DELETE FROM attempts'):
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                c.execute(sql)
        assert {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {'answers', 'attempts'}


def test_failed_answers_are_not_cached_and_missing_digest_changes_on_fill(tmp_path):
    path = tmp_path/'answers.sqlite3'
    with AnswerCache(path, writable=True) as cache:
        before = cache.used_digest([STATE])
        with pytest.raises(ValueError, match='invalid'):
            cache.put(STATE, {'model': MODEL})
        assert cache.get(STATE) is None
        cache.record_attempt(STATE, 'invalid_response')
        cache.put(STATE, payload())
        assert cache.used_digest([STATE]) != before


def test_cache_rejects_corrupt_response_instead_of_reusing_same_identity(tmp_path):
    path = tmp_path/'answers.sqlite3'
    with AnswerCache(path, writable=True) as cache:
        cache.put(STATE, payload())
    with sqlite3.connect(path) as c:
        c.execute('DROP TRIGGER answers_no_update')
        c.execute('UPDATE answers SET response_json=?', (json.dumps({'bad': True}),))
    with AnswerCache(path) as cache:
        with pytest.raises(ValueError, match='checksum'):
            cache.get(STATE)
