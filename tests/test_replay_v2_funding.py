from datetime import datetime, timedelta, timezone
import json
import stat

import pytest

from intraday.replay_v2.funding import fetch_funding_snapshot, save_funding_snapshot, read_funding_snapshot


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def row(at, symbol="ETHUSDT", rate=".0001"):
    return {"symbol":symbol, "fundingTime":int(at.timestamp()*1000),
            "fundingRate":rate, "markPrice":"100", "rateType":"Regular"}


def test_funding_pagination_proves_requested_window_and_preserves_raw_values(tmp_path):
    calls = []
    first = row(NOW)
    second = row(NOW+timedelta(hours=4), rate="-.0002")
    def fetch(query):
        calls.append(query)
        return [first] if len(calls) == 1 else [second] if len(calls) == 2 else []
    snapshot = fetch_funding_snapshot("ETH", NOW, NOW+timedelta(days=1), fetch_json=fetch, page_size=1, now=NOW+timedelta(days=2))
    assert len(calls) == 3
    assert calls[1]["startTime"] == first["fundingTime"]+1
    assert calls[0]["endTime"] == int((NOW+timedelta(days=1)).timestamp()*1000)-1
    assert str(snapshot.history.settlements[1].rate) == "-0.0002"
    saved = save_funding_snapshot(tmp_path, snapshot)
    assert read_funding_snapshot(tmp_path, saved["funding_id"]) == snapshot
    path = tmp_path/"funding"/(saved["funding_id"]+".json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    payload = json.loads(path.read_text())
    payload["history"]["settlements"][0]["rate"] = "0"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="checksum"):
        read_funding_snapshot(tmp_path, saved["funding_id"])


@pytest.mark.parametrize("bad", [row(NOW, symbol="BTCUSDT"), row(NOW, rate="NaN"),
                                     row(NOW-timedelta(hours=1)), {"code":-1}])
def test_wrong_symbol_nonfinite_out_of_window_and_api_errors_fail_closed(bad):
    with pytest.raises(ValueError):
        fetch_funding_snapshot("ETH", NOW, NOW+timedelta(days=1), fetch_json=lambda q:[bad], now=NOW+timedelta(days=2))


def test_empty_history_is_not_declared_complete():
    with pytest.raises(ValueError, match="empty"):
        fetch_funding_snapshot("ETH", NOW, NOW+timedelta(days=1), fetch_json=lambda q:[], now=NOW+timedelta(days=2))


def test_duplicates_and_timeouts_never_publish_partial_coverage():
    with pytest.raises(ValueError):
        fetch_funding_snapshot("ETH", NOW, NOW+timedelta(days=1), fetch_json=lambda q:[row(NOW), row(NOW)], now=NOW+timedelta(days=2))
    with pytest.raises(TimeoutError):
        fetch_funding_snapshot("ETH", NOW, NOW+timedelta(days=1), fetch_json=lambda q: (_ for _ in ()).throw(TimeoutError()), now=NOW+timedelta(days=2))
