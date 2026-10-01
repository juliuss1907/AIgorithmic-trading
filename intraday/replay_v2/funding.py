"""Explicit public funding collection; the replay engine itself remains offline."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

from pydantic import Field, model_validator

from intraday.assets import ticker_symbol
from intraday.replay_v2.contracts import FrozenModel, FundingHistory, FundingSettlement, utc
from intraday.replay_v2.metrics import encoded, fingerprint


SOURCE = "https://fapi.binance.com/fapi/v1/fundingRate"
MAX_BYTES = 20_000_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def public_funding_json(query):
    request = Request(SOURCE+"?"+urlencode(query), headers={"User-Agent":"aigt-offline-funding/1"})
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        body = response.read(MAX_BYTES+1)
    if len(body) > MAX_BYTES:
        raise ValueError("funding response exceeds size limit")
    return json.loads(body)


class FundingSnapshot(FrozenModel):
    schema_version: str = "1"
    funding_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    fetched_at: datetime
    pages: int = Field(ge=1)
    history: FundingHistory
    raw_rows: tuple[dict, ...]

    @model_validator(mode="after")
    def verify(self):
        if self.schema_version != "1" or self.history.source != SOURCE or utc(self.fetched_at) < self.history.coverage_end:
            raise ValueError("invalid funding snapshot source or coverage")
        payload = self.model_dump(mode="json", exclude={"funding_id"})
        if fingerprint(payload) != self.funding_id:
            raise ValueError("funding snapshot checksum mismatch")
        settlements = _parse_rows(self.raw_rows, self.history.symbol,
                                  self.history.coverage_start, self.history.coverage_end)
        if not settlements or settlements != self.history.settlements:
            raise ValueError("funding snapshot raw evidence mismatch")
        return self


def _parse_rows(rows, symbol, start, end):
    parsed = []
    previous = None
    for row in rows:
        if not isinstance(row, dict) or row.get("symbol") != symbol or row.get("rateType", "Regular") != "Regular":
            raise ValueError("invalid funding symbol or settlement type")
        milliseconds = row.get("fundingTime")
        if isinstance(milliseconds, bool) or not isinstance(milliseconds, int):
            raise ValueError("invalid funding timestamp")
        at = datetime.fromtimestamp(milliseconds/1000, timezone.utc)
        if not start <= at < end or previous is not None and at <= previous:
            raise ValueError("funding history must be unique, ordered and inside requested window")
        parsed.append(FundingSettlement(at=at, rate=row.get("fundingRate"), mark=row.get("markPrice")))
        previous = at
    return tuple(parsed)


def fetch_funding_snapshot(symbol, start, end, *, fetch_json=public_funding_json, page_size=1000, now=None):
    symbol, start, end = ticker_symbol(symbol), utc(start), utc(end)
    observed = utc(now or datetime.now(timezone.utc))
    if not start < end <= observed or not 1 <= page_size <= 1000:
        raise ValueError("funding collection requires a completed past window and valid page size")
    cursor, end_ms = int(start.timestamp()*1000), int(end.timestamp()*1000)-1
    rows, pages = [], 0
    while cursor <= end_ms:
        page = fetch_json({"symbol":symbol, "startTime":cursor, "endTime":end_ms, "limit":page_size})
        pages += 1
        if not isinstance(page, list) or len(page) > page_size:
            raise ValueError("invalid funding API response")
        if not page:
            break
        _parse_rows(page, symbol, datetime.fromtimestamp(cursor/1000, timezone.utc), end)
        rows.extend(page)
        cursor = page[-1]["fundingTime"]+1
        if len(page) < page_size:
            break
        if pages >= 10_000:
            raise ValueError("funding pagination limit exceeded")
    if not rows:
        raise ValueError("empty funding history cannot prove coverage")
    history = FundingHistory(symbol=symbol, source=SOURCE, coverage_start=start,
                             coverage_end=end, settlements=_parse_rows(rows, symbol, start, end))
    payload = {"schema_version":"1", "fetched_at":observed.isoformat().replace("+00:00", "Z"), "pages":pages,
               "history":history.model_dump(mode="json"), "raw_rows":rows}
    return FundingSnapshot(funding_id=fingerprint(payload), **payload)


def save_funding_snapshot(root, snapshot):
    directory = Path(root).expanduser().resolve()/"funding"
    if directory.is_symlink():
        raise ValueError("funding directory cannot be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory/(snapshot.funding_id+".json")
    if path.exists():
        if read_funding_snapshot(root, snapshot.funding_id) != snapshot:
            raise ValueError("funding snapshot identity collision")
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(encoded(snapshot.model_dump(mode="json")))
            stream.flush()
            os.fsync(stream.fileno())
    return {"funding_id":snapshot.funding_id, "symbol":snapshot.history.symbol,
            "coverage_start":snapshot.history.coverage_start.isoformat(),
            "coverage_end":snapshot.history.coverage_end.isoformat(),
            "settlements":len(snapshot.history.settlements)}


def read_funding_snapshot(root, funding_id):
    if not re.fullmatch(r"[a-f0-9]{64}", funding_id):
        raise ValueError("invalid funding snapshot ID")
    directory = Path(root).expanduser().resolve()/"funding"
    if directory.is_symlink():
        raise ValueError("funding directory cannot be a symlink")
    fd = os.open(directory/(funding_id+".json"), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        body = stream.read(MAX_BYTES+1)
    if len(body) > MAX_BYTES:
        raise ValueError("funding snapshot exceeds size limit")
    snapshot = FundingSnapshot.model_validate_json(body)
    if snapshot.funding_id != funding_id:
        raise ValueError("funding snapshot ID mismatch")
    return snapshot
