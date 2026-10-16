from decimal import Decimal

from intraday.jev_review_budget import CollectionBudget, RESERVATION, response_cost
from intraday.jev_review_cache import AnswerCache
from intraday.jev_review_provider import ReviewCall, collect_missing
from test_jev_review_provider import TOKENS
from test_setup2_review import payload


def test_budget_reserves_before_call_and_survives_resume(tmp_path):
    class Provider:
        def request(self, state):
            raise AssertionError('budget must stop before network')
    path = tmp_path/'cache.sqlite3'
    with AnswerCache(path, writable=True) as cache:
        budget = CollectionBudget(cache, Decimal(RESERVATION)/1_000_000_000)
        assert budget.reserve() is not None  # Interrupted call remains charged.
    with AnswerCache(path, writable=True) as cache:
        budget = CollectionBudget(cache, Decimal(RESERVATION)/1_000_000_000)
        out = collect_missing([TOKENS], cache, Provider(), budget=budget)
        assert out['stopped_reason'] == 'cost_limit' and out['network_calls'] == 0
        assert out['remaining'] == 1


def test_usage_releases_reservation_but_missing_usage_does_not(tmp_path):
    with AnswerCache(tmp_path/'cache.sqlite3', writable=True) as cache:
        budget = CollectionBudget(cache)
        first = budget.reserve()
        budget.settle(first, response_cost({'usage': {'prompt_tokens': 500}}))
        second = budget.reserve()
        budget.settle(second, response_cost({'usage': {'cost': False}}))
        receipt = budget.payload()
        assert Decimal(receipt['reported_or_token_cost_usd']) == Decimal('.000021')
        assert Decimal(receipt['unresolved_reservations_usd']) == Decimal(RESERVATION)/1_000_000_000
    assert response_cost({'usage': {'cost': float('nan')}}) is None
    assert response_cost({'usage': {'input_tokens': True}}) is None


def test_retry_is_charged_even_without_success(tmp_path):
    class Provider:
        def request(self, state):
            return ReviewCall('provider_unavailable')
    with AnswerCache(tmp_path/'cache.sqlite3', writable=True) as cache:
        budget = CollectionBudget(cache, Decimal(RESERVATION)/1_000_000_000)
        out = collect_missing([TOKENS], cache, Provider(), budget=budget, sleep=lambda _: None)
        assert out['network_calls'] == 1 and out['stopped_reason'] == 'cost_limit'
        assert budget.payload()['reserved_calls'] == 1
