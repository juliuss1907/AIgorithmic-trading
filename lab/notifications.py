"""Fail-open Telegram delivery with a fixed, secret-safe API boundary."""

import json
import os
import re
import stat
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TOKEN_PATTERN = re.compile(r"^[1-9][0-9]{5,15}:[A-Za-z0-9_-]{16,128}$")
CHAT_PATTERN = re.compile(r"^(?:-?[0-9]{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")


class TelegramDeliveryError(RuntimeError):
    """A sanitized Telegram error that never includes the bot token."""


class TelegramNotifier:
    api_origin = "https://api.telegram.org"

    def __init__(self, token, chat_id, *, post_json=None, sleep=time.sleep):
        if not TOKEN_PATTERN.fullmatch(str(token)):
            raise ValueError("Telegram has an invalid bot token")
        if not CHAT_PATTERN.fullmatch(str(chat_id)):
            raise ValueError("Telegram has an invalid chat id")
        self._token = str(token)
        self.chat_id = str(chat_id)
        self._post_json = post_json or self._default_post_json
        self._sleep = sleep

    @staticmethod
    def _default_post_json(request, timeout):
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read(65_537)
        except HTTPError:
            raise
        except (OSError, TimeoutError, URLError) as exc:
            raise TelegramDeliveryError("Telegram request failed") from exc
        if len(body) > 65_536:
            raise TelegramDeliveryError("Telegram returned an oversized response")
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TelegramDeliveryError("Telegram returned invalid JSON") from exc

    def send(self, text):
        if not isinstance(text, str) or not 1 <= len(text) <= 4096:
            raise ValueError("Telegram text must contain 1 to 4096 characters")
        request = Request(
            f"{self.api_origin}/bot{self._token}/sendMessage",
            data=json.dumps({"chat_id": self.chat_id, "text": text}).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        last_error = None
        for attempt in range(3):
            try:
                payload = self._post_json(request, 10)
                if not isinstance(payload, dict) or payload.get("ok") is not True:
                    raise TelegramDeliveryError("Telegram rejected the request")
                result = payload.get("result")
                if not isinstance(result, dict):
                    raise TelegramDeliveryError("Telegram returned an invalid result")
                return result
            except HTTPError as exc:
                last_error = TelegramDeliveryError(
                    f"Telegram request failed with HTTP {exc.code}"
                )
            except (OSError, TimeoutError, URLError):
                last_error = TelegramDeliveryError("Telegram request failed")
            except TelegramDeliveryError as exc:
                last_error = exc
            if attempt < 2:
                self._sleep(2 ** attempt)
        raise last_error


def telegram_from_environment(environ=None):
    environ = os.environ if environ is None else environ
    token = environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = environ.get("TELEGRAM_CHAT_ID")
    if not token and not chat_id:
        return None
    if not token or not chat_id:
        raise ValueError("Telegram alert configuration is incomplete")
    return TelegramNotifier(token, chat_id)


def telegram_from_file(path):
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError("Telegram config must be a readable regular file") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Telegram config must be a readable regular file")
        if metadata.st_uid != os.getuid():
            raise ValueError("Telegram config must be owned by the current user")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("Telegram config must have mode 600")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(4097)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw) > 4096:
        raise ValueError("Telegram config is too large")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Telegram config must be UTF-8") from exc
    values = {}
    allowed = {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError("Telegram config contains an invalid line")
        key, value = stripped.split("=", 1)
        if key not in allowed or key in values:
            raise ValueError("Telegram config contains an invalid key")
        values[key] = value
    return telegram_from_environment(values)
