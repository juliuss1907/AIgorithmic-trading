from datetime import datetime, timezone

from fastapi.testclient import TestClient

from intraday.replay_v2.confidence_reviews import ReviewStore
from intraday.web import create_app
from test_replay_v2_data import dump


NOW = datetime(2026, 10, 5, 2, tzinfo=timezone.utc)


def test_dashboard_reads_proposals_without_model_calls_or_creating_report_root(tmp_path):
    root = tmp_path/"missing"
    database = tmp_path/"source.sqlite"
    client = TestClient(create_app(database=database,replay_report_dir=root))
    before = dump(database)
    response = client.get("/api/replay/confidence")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 4
    assert all(item["proposal"] is None for item in response.json()["items"])
    assert not root.exists()
    assert dump(database) == before
    assert client.post("/api/replay/confidence",json={}).status_code == 405
    assert client.get("/api/replay/confidence?symbol=../../invalid").status_code == 400
    page = client.get("/assets").text
    assert "Confidence proposals" in page
    assert "confidence-proposals.js" in page


def test_proposal_detail_is_read_only_and_current_is_not_proposed(tmp_path):
    root = tmp_path/"reports"
    reviews = ReviewStore(root)
    reviews.claim("ETH",NOW,{})
    reviews.finish("ETH",NOW,{"status":"pending_review","current_threshold":.85,
        "proposed_threshold":.732,"proposal":{"rationale":"<script>untrusted</script>"},
        "model_ref":"fake-model","blockers":[],"holdout":{"run_id":"a"*32,"summary":{
            "net_return_pct":1,"max_drawdown_known_pct":2,"closed_trades":10}}})
    client = TestClient(create_app(database=tmp_path/"source.sqlite",replay_report_dir=root))
    from test_replay_v2_perp import inputs, quote, decision
    _, data = inputs([quote(0), quote(10)], [decision()])
    client.app.state.store.register_scoped_rule(data.rule)
    before = reviews.path.read_bytes()
    payload = client.get("/api/replay/confidence?symbol=ETH").json()["items"][0]
    assert payload["proposal"]["proposed_threshold"] == .732
    assert payload["current_threshold"] == .85  # Actual source rule, not proposal value.
    assert payload["activation_allowed"] is False
    assert reviews.path.read_bytes() == before
    script = client.get("/static/confidence-proposals.js").text
    assert "textContent" in script and "innerHTML" not in script
    assert "Asia/Ho_Chi_Minh" in script
