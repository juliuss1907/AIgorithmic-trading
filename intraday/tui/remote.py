"""The VPS as `aigt ui` sees it: the web API read through an SSH tunnel, and `aigt` run on the host over SSH.

No Textual here, so the tunnel and the command runner are testable on their own."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from intraday.deployment import deployment_registry_path

WEB_PORT = 8081                              # the web container's port on the VPS loopback
DEFAULT_PORT = 18081                         # the local end of the tunnel
REMOTE_AIGT = "~/.local/bin/aigt"            # `uv tool install` on the VPS


def remote_config_path() -> Path:
    return deployment_registry_path().with_name("remote.toml")


def load_host() -> str | None:
    try:
        with remote_config_path().open("rb") as handle:
            return tomllib.load(handle).get("host")
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return None


def save_host(host: str) -> Path:
    path = remote_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"host = {json.dumps(host)}\n")
    return path


class RemoteError(RuntimeError):
    pass


@dataclass
class Remote:
    host: str | None
    port: int = DEFAULT_PORT
    timeout: float = 60.0
    tunnel: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def get(self, path: str) -> dict | list:
        try:
            with urllib.request.urlopen(self.base_url+path, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RemoteError(f"{path}: HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise RemoteError(f"{path}: {getattr(error, 'reason', error)}") from error

    def healthy(self) -> bool:
        try:
            return self.get("/healthz").get("status") == "ok"
        except RemoteError:
            return False

    def open_tunnel(self, *, wait: float = 15.0, popen: Callable = subprocess.Popen) -> str:
        """`existing` when the web answers already, `opened` after starting `ssh -N -L`; RemoteError otherwise."""
        if self.healthy():
            return "existing"
        if not self.host:
            raise RemoteError(f"nothing answers on {self.base_url} and no host is set (aigt ui --host user@vps)")
        self.tunnel = popen(
            ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
             "-L", f"127.0.0.1:{self.port}:127.0.0.1:{WEB_PORT}", self.host],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic()+wait
        while time.monotonic() < deadline:
            if self.tunnel.poll() is not None:
                message = (self.tunnel.stderr.read() or b"").decode(errors="replace").strip()
                self.tunnel = None
                raise RemoteError(f"ssh tunnel to {self.host} failed: {message or 'exited'}")
            if self.healthy():
                return "opened"
            time.sleep(0.3)
        self.close()
        raise RemoteError(f"ssh tunnel to {self.host} opened but the web did not answer in {wait:.0f}s")

    def close(self) -> None:
        if self.tunnel is not None and self.tunnel.poll() is None:
            self.tunnel.terminate()
            try:
                self.tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.tunnel.kill()
        self.tunnel = None

    def command(self, arguments: list[str]) -> list[str]:
        """The local argv that runs `aigt <arguments>` on the host; each argument is quoted for the remote shell."""
        if not self.host:
            raise RemoteError("no host is set (aigt ui --host user@vps)")
        return ["ssh", "-o", "BatchMode=yes", self.host, " ".join([REMOTE_AIGT, *map(shlex.quote, arguments)])]

    def run(self, arguments: list[str], *, popen: Callable = subprocess.Popen) -> Iterator[str]:
        """Lines of stdout and stderr of `aigt <arguments>` on the host, then `[exit N]`."""
        process = popen(self.command(arguments), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in process.stdout:
            yield line.rstrip("\n")
        yield f"[exit {process.wait()}]"
