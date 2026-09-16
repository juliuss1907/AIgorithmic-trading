import json
from urllib.error import HTTPError

import pytest

from lab.notifications import TelegramDeliveryError, TelegramNotifier, telegram_from_environment


def test_telegram_notifier_posts_plain_text_to_fixed_bot_api():
    requests = []

    def post(request, timeout):
        requests.append((request, timeout))
        return {"ok": True, "result": {"message_id": 42}}

    notifier = TelegramNotifier("123456:secret-token_value", "-100123", post_json=post)

    result = notifier.send("Paper cycle completed")

    request, timeout = requests[0]
    assert request.full_url == (
        "https://api.telegram.org/bot123456:secret-token_value/sendMessage"
    )
    assert request.method == "POST"
    assert request.headers["Content-type"] == "application/json"
    assert json.loads(request.data) == {
        "chat_id": "-100123",
        "text": "Paper cycle completed",
    }
    assert timeout == 10
    assert result["message_id"] == 42


def test_telegram_notifier_retries_and_never_exposes_token_in_error():
    token = "123456:never-print-this-token"
    attempts = []
    sleeps = []

    def post(request, _timeout):
        attempts.append(request)
        raise HTTPError(request.full_url, 503, "provider unavailable", {}, None)

    notifier = TelegramNotifier(token, "123", post_json=post, sleep=sleeps.append)

    with pytest.raises(TelegramDeliveryError) as caught:
        notifier.send("daily summary")

    assert len(attempts) == 3
    assert sleeps == [1, 2]
    assert token not in str(caught.value)
    assert "503" in str(caught.value)


def test_telegram_notifier_validates_secret_config_and_message_bounds():
    assert telegram_from_environment({}) is None
    with pytest.raises(ValueError, match="incomplete"):
        telegram_from_environment({"TELEGRAM_BOT_TOKEN": "123456:secret-token_value"})
    with pytest.raises(ValueError, match="invalid bot token"):
        TelegramNotifier("bad token", "123")
    with pytest.raises(ValueError, match="invalid chat id"):
        TelegramNotifier("123456:secret-token_value", "not a chat")

    notifier = TelegramNotifier(
        "123456:secret-token_value", "123", post_json=lambda *_: {"ok": True, "result": {}}
    )
    with pytest.raises(ValueError, match="1 to 4096"):
        notifier.send("")
    with pytest.raises(ValueError, match="1 to 4096"):
        notifier.send("x" * 4097)


def test_telegram_notifier_rejects_unsuccessful_json_without_echoing_description():
    token = "123456:another-secret-token"
    notifier = TelegramNotifier(
        token,
        "123",
        post_json=lambda *_: {"ok": False, "description": f"bad request {token}"},
        sleep=lambda *_: None,
    )

    with pytest.raises(TelegramDeliveryError) as caught:
        notifier.send("daily summary")

    assert token not in str(caught.value)
    assert str(caught.value) == "Telegram rejected the request"
