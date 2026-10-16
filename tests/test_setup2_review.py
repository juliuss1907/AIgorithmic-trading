from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import re

import pytest

from intraday.setup2 import Bar, Setup2Observation
from intraday.setup2_review import (
    VOCABULARY, ReviewAnswer, LabelConfig, encode_state, label_a, questions, parse_answer,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def observation():
    return Setup2Observation(1, NOW-timedelta(hours=4), NOW, True, False, True,
        close=D(120), atr=D(2), evaluator_version='setup2-slots-live-v1',
        features=dict(adx=D(35), prior_adx=D(30), ema200=D(100), ema50=D(110), prior_ema50=D(109),
                      volume=D(250), volume_ma=D(100)), profile=dict(vah=115, val=100))


def history():
    return [Bar(NOW-timedelta(hours=4*(40-i)), NOW-timedelta(hours=4*(39-i)),
                D(80+i), D(82+i), D(79+i), D(81+i), D(100)) for i in range(40)]


def payload(**updates):
    result = dict(model='typesafe/jev-1.13-20260917', answers=dict(
        action=dict(choice='enter_full', probabilities=dict(enter_full=.7, reduce=.2, skip=.1)),
        exhaustion=dict(noul=.2), disorderly=dict(noul=.1)))
    result.update(updates)
    return result


def test_encoder_is_fixed_letter_only_vocabulary_and_causal():
    obs, bars = observation(), history()
    tokens = encode_state(obs, bars)
    assert len(tokens) == 14 and set(tokens) <= VOCABULARY
    assert all(re.fullmatch('[a-z_]+', t) for t in tokens)
    assert encode_state(obs, bars) == tokens
    assert 'spot_long' in tokens and 'breakout_twenty_five' in tokens
    assert 'ema_slow_far' in tokens and 'rsi_overbought' in tokens
    future = replace(bars[-1], opened_at=NOW, available_at=NOW+timedelta(hours=4), close=D(1))
    assert encode_state(obs, bars+[future]) == tokens


def test_encoder_missing_and_nonfinite_features_are_unknown():
    obs = Setup2Observation(-1, None, None, False, False, False)
    tokens = encode_state(obs, [])
    assert len(tokens) == 14 and 'perp_short' in tokens
    assert all(t.endswith('_unknown') for t in tokens if t != 'perp_short')
    obs = replace(observation(), atr=D('NaN'), features={'adx': D('Infinity')})
    assert 'atr_unknown' in encode_state(obs, history())
    assert 'adx_unknown' in encode_state(obs, history())


def test_questions_have_three_atomic_judgments_and_multi_day_stops():
    q = questions()
    assert set(q) == {'action', 'exhaustion', 'disorderly'}
    assert q['action']['type'] == 'choice'
    assert set(q['action']['criteria']) == {'enter_full', 'reduce', 'skip'}
    assert q['exhaustion']['type'] == q['disorderly']['type'] == 'noul'
    assert all('multi-day' in v['instructions'] for v in q.values())
    assert 'five percent' in str(q) and 'ten percent' in str(q)


def test_questions_v2_remove_only_the_leading_rsi_sentence():
    from intraday.setup2_review import QUESTION_SET_VERSION
    from intraday.jev_review_cache import digest
    assert QUESTION_SET_VERSION == 'setup2-review-questions-v2'
    assert digest(questions()) == 'ee1b7367ad9228d4f1accd94e75515052468354b0da0a473b40e6125468fab60'


def test_parser_accepts_complete_distribution_and_records_returned_model():
    answer = parse_answer(payload())
    assert answer == ReviewAnswer('typesafe/jev-1.13-20260917', D('.7'), D('.2'), D('.1'), D('.2'), D('.1'))


@pytest.mark.parametrize('change', ['missing', 'nan', 'bool', 'string', 'sum', 'choice', 'model', 'extra'])
def test_parser_returns_no_view_for_malformed_answer(change):
    p = payload()
    if change == 'missing': del p['answers']['exhaustion']
    if change == 'nan': p['answers']['disorderly']['noul'] = float('nan')
    if change == 'bool': p['answers']['exhaustion']['noul'] = True
    if change == 'string': p['answers']['exhaustion']['noul'] = '0.2'
    if change == 'sum': p['answers']['action']['probabilities']['skip'] = .6
    if change == 'choice': p['answers']['action']['choice'] = 'skip'
    if change == 'model': del p['model']
    if change == 'extra': p['answers']['other'] = dict(noul=.1)
    assert parse_answer(p) is None


def test_label_order_threshold_boundaries_and_never_increases_size():
    cfg = LabelConfig()
    assert label_a(None, cfg) == ('NO_VIEW', D(1))
    assert label_a(ReviewAnswer('m', D('.2'), D('.1'), D('.7'), D('.8'), D('.9')), cfg) == ('VETO', D(0))
    assert label_a(ReviewAnswer('m', D('.4'), D('.5'), D('.1'), D('.2'), D('.1')), cfg) == ('REDUCE', D('.5'))
    assert label_a(ReviewAnswer('m', D('.8'), D('.1'), D('.1'), D('.1'), D('.1')), cfg) == ('AGREE', D(1))
    for value in (D(0), D('.6'), D(1)):
        label, size = label_a(ReviewAnswer('m', D(1), D(0), D(0), value, value), cfg)
        assert size in (D(0), D('.5'), D(1))
