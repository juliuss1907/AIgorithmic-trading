from datetime import datetime, timedelta, timezone
import pytest

from intraday.replay_v2.confidence_reviews import ReviewStore, weekly_slot, due_weekly


NOW = datetime(2026, 10, 5, 2, tzinfo=timezone.utc)


def test_weekly_due_uses_monday_nine_vietnam():
    assert due_weekly(NOW)
    assert not due_weekly(NOW-timedelta(seconds=1))
    assert weekly_slot(NOW) == "2026-10-05"
    assert weekly_slot(NOW+timedelta(days=2)) == weekly_slot(NOW)
    assert not due_weekly(NOW+timedelta(days=2))


def test_read_only_review_status_does_not_create_files(tmp_path):
    root = tmp_path/"missing"
    store = ReviewStore(root)
    assert store.latest("ETH") is None
    assert not root.exists()


def test_durable_claim_prevents_second_model_attempt_and_failure_retry(tmp_path):
    store = ReviewStore(tmp_path)
    assert store.claim("ETH", NOW, {"current_threshold": .85})
    assert not store.claim("ETH", NOW+timedelta(hours=1), {"current_threshold": .85})
    store.finish("ETH", NOW, {"status": "error", "blockers": ["model_unavailable"]})
    assert not store.claim("ETH", NOW, {})
    assert store.latest("ETH")["status"] == "error"
    assert store.latest("SOL") is None


def test_passing_pending_proposal_blocks_future_week_until_explicit_dismiss(tmp_path):
    store = ReviewStore(tmp_path)
    assert store.claim("ETH", NOW, {})
    store.finish("ETH", NOW, {"status": "pending_review", "proposed_threshold": .732})
    assert not store.claim("ETH", NOW+timedelta(days=7), {})
    review = store.latest("ETH")
    store.dismiss("ETH", review["review_id"], now=NOW+timedelta(days=1))
    assert store.latest("ETH")["status"] == "dismissed"
    assert store.claim("ETH", NOW+timedelta(days=7), {})


def test_review_integrity_and_wrong_symbol_dismiss_are_rejected(tmp_path):
    store = ReviewStore(tmp_path)
    store.claim("ETH", NOW, {})
    with pytest.raises(ValueError):
        store.dismiss("SOL", store.latest("ETH")["review_id"], now=NOW)
    with store.connect(write=True) as c:
        c.execute("UPDATE reviews SET payload_json='{\"unexpected\":1}'")
    with pytest.raises(ValueError, match="checksum"):
        store.latest("ETH")
