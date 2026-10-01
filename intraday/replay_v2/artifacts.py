"""Private, immutable report bundles, published by an atomic final manifest."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from intraday.config import default_database_path
from intraday.replay_v2.contracts import utc
from intraday.replay_v2.metrics import encoded


SERIES = ("equity_curve", "trades", "events")
PUBLIC_FIELDS = ("schema_version", "evaluator_version", "result_id", "research_only", "activation_allowed",
                 "status", "config", "inputs", "summary", "limitations", "methodology", "v1_reference")
ID_PATTERN = re.compile(r"[a-f0-9]{32}")
MAX_SUMMARY_BYTES = 2_000_000


def resolve_report_dir(directory=None):
    configured = directory or os.getenv("INTRADAY_REPLAY_REPORT_DIR")
    return Path(configured).expanduser().resolve() if configured else default_database_path().parent/"reports"/"replay-v2"


def _write(path, text):
    data = text.encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def markdown_summary(report):
    config, summary = report["config"], report["summary"]
    net = summary.get("net_pnl")
    return (f"# Replay v2 — {config['symbol']} {config['market']}\n\n"
        "Offline research only; not an activation or a v1 gate evaluation.\n\n"
        f"Window UTC: {config['start']} → {config['end']}\n\n"
        f"Net PnL: {net if net is not None else 'UNKNOWN (incomplete funding/data)'} USDT\n\n"
        f"PnL after known costs: {summary['pnl_after_known_costs']} USDT\n\n"
        f"Drawdown (known costs): {summary['max_drawdown_known_pct']}%\n\n"
        f"Closed trades: {summary['closed_trades']}\n\n"
        "## Assumptions and limitations\n\n" + "\n".join(f"- {item}" for item in report["limitations"]) +
        "\n\nCosts are profile assumptions, not verified account fees. UTC daily risk; "
        "no exact liquidation/order-book/partial-fill simulation.\n")


def publish_report(root, report, *, now=None):
    if report.get("schema_version") != "2" or report.get("research_only") is not True or report.get("activation_allowed") is not False:
        raise ValueError("only research-only Replay v2 reports can be published")
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_id = uuid.uuid4().hex
    directory = root/run_id
    directory.mkdir(mode=0o700)
    public = {name: report[name] for name in PUBLIC_FIELDS}
    curve = report["equity_curve"]
    indexes = sorted({i*(len(curve)-1)//199 for i in range(200)}) if curve else []
    public["equity_preview"] = [{name:curve[i][name] for name in ("at", "equity_known")} for i in indexes]
    body = encoded(public)
    if len(body.encode()) > MAX_SUMMARY_BYTES:
        raise ValueError("replay summary exceeds report size limit")
    files = {"summary.json": _write(directory/"summary.json", body),
             "summary.md": _write(directory/"summary.md", markdown_summary(report))}
    for name in SERIES:
        path = directory/f"{name}.jsonl"
        digest, size = hashlib.sha256(), 0
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            for item in report[name]:
                line = (encoded(item)+"\n").encode()
                if len(line) > 16_384:
                    raise ValueError("replay ledger row exceeds report size limit")
                stream.write(line)
                digest.update(line)
                size += len(line)
            stream.flush()
            os.fsync(stream.fileno())
        files[path.name] = {"sha256": digest.hexdigest(), "bytes": size, "rows": len(report[name])}
    manifest = {"schema_version":"2", "run_id":run_id,
                "created_at":utc(now or datetime.now(timezone.utc)).isoformat(), "files":files}
    # Readers ignore a directory until this final atomic publication succeeds.
    _write(directory/"manifest.pending", encoded(manifest))
    os.replace(directory/"manifest.pending", directory/"manifest.json")
    return {"run_id":run_id, "report_directory":str(directory), "result_id":report["result_id"],
            "status":report["status"], "summary":report["summary"], "activation_allowed":False}


def _read_small(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        data = stream.read(MAX_SUMMARY_BYTES+1)
    if len(data) > MAX_SUMMARY_BYTES:
        raise ValueError("replay metadata too large")
    return data


def _manifest(root, run_id):
    if not ID_PATTERN.fullmatch(run_id):
        raise ValueError("invalid replay run ID")
    root = Path(root).expanduser().resolve()
    directory = root/run_id
    if directory.is_symlink() or directory.resolve().parent != root:
        raise ValueError("invalid replay report path")
    manifest = json.loads(_read_small(directory/"manifest.json"))
    if manifest["run_id"] != run_id or manifest["schema_version"] != "2":
        raise ValueError("replay manifest identity mismatch")
    utc(datetime.fromisoformat(manifest["created_at"]))
    return directory, manifest


@contextmanager
def _verified_file(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        digest, size = hashlib.sha256(), 0
        while chunk := stream.read(65_536):
            digest.update(chunk)
            size += len(chunk)
        if size != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
            raise ValueError("replay artifact checksum mismatch")
        stream.seek(0)
        yield stream


def read_report(root, run_id):
    directory, manifest = _manifest(root, run_id)
    with _verified_file(directory/"summary.json", manifest["files"]["summary.json"]) as stream:
        data = stream.read(MAX_SUMMARY_BYTES+1)
    if len(data) > MAX_SUMMARY_BYTES:
        raise ValueError("replay summary too large")
    report = json.loads(data)
    if report["schema_version"] != "2" or report["research_only"] is not True or report["activation_allowed"] is not False:
        raise ValueError("invalid research report")
    return {"run_id":run_id, "created_at":manifest["created_at"],
            **{name:report[name] for name in PUBLIC_FIELDS},
            "equity_preview":report.get("equity_preview", []),
            "series_counts":{name:manifest["files"][f"{name}.jsonl"]["rows"] for name in SERIES}}


def list_reports(root, *, offset=0, limit=20):
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("invalid replay report pagination")
    root = Path(root)
    items = []
    for path in root.iterdir() if root.is_dir() else ():
        if not ID_PATTERN.fullmatch(path.name) or path.is_symlink():
            continue
        try:
            report = read_report(root, path.name)
            items.append({name:report[name] for name in ("run_id", "created_at", "status", "summary", "limitations", "evaluator_version")} |
                         {name:report["config"][name] for name in ("symbol", "market", "start", "end")})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    items.sort(key=lambda item:(item["created_at"], item["run_id"]), reverse=True)
    return {"items":items[offset:offset+limit], "total":len(items), "offset":offset, "limit":limit}


def read_series(root, run_id, name, *, offset=0, limit=200):
    if name not in SERIES or offset < 0 or not 1 <= limit <= 1000:
        raise ValueError("invalid replay series or pagination")
    read_report(root, run_id)
    directory, manifest = _manifest(root, run_id)
    expected = manifest["files"][f"{name}.jsonl"]
    items = []
    with _verified_file(directory/f"{name}.jsonl", expected) as stream:
        for index in range(offset+limit):
            line = stream.readline(16_385)
            if not line:
                break
            if len(line) > 16_384:
                raise ValueError("replay ledger row too large")
            if index >= offset:
                items.append(json.loads(line))
    return {"items":items, "total":expected["rows"], "offset":offset, "limit":limit}
