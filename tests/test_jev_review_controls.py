import pytest
from decimal import Decimal

from intraday.jev_review import build_plan, route_identity, scan_route
from intraday.jev_review_cache import AnswerCache
from scripts.verify_jev_review_controls import bound_signals
from test_jev_review_overlay import route


def test_controls_reject_changed_dataset_or_stale_signals_before_artifacts(route, tmp_path):
    config, data, _ = route
    signals = scan_route(config, data)
    with AnswerCache(tmp_path/'absent.sqlite3') as cache:
        plan = build_plan(signals, cache, route_inputs=[route_identity(config, data, signals)])
    assert bound_signals(plan, [(config, data)]) == signals
    changed = config.model_copy(update={'capital': Decimal(999)})
    with pytest.raises(ValueError, match='route'):
        bound_signals(plan, [(changed, data)])
    bad = dict(plan, signals=[])
    with pytest.raises(ValueError, match='signal'):
        bound_signals(bad, [(config, data)])
