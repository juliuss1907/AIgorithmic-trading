"""Local provider credentials kept outside SQLite and the repository.

The secret file deliberately has a small, dependency-free TOML format. Every
read verifies ownership and permissions; every write replaces the file
atomically with mode 0600.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from intraday.contracts import ProviderProfile


def validate_provider_api_key(api_key: str) -> str:
    if not api_key or api_key != api_key.strip():
        raise ValueError("provider API key must be nonempty without surrounding whitespace")
    if len(api_key) > 4096 or any(character in api_key for character in "\r\n\0"):
        raise ValueError("provider API key contains invalid characters")
    return api_key


@dataclass(frozen=True)
class ProviderCredential:
    profile: ProviderProfile
    api_key: str = field(repr=False)


class ProviderSecretStore:
    """Secure, process-local access to provider profile credentials."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def _validate_existing_file(self) -> None:
        try:
            details = self.path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(details.st_mode):
            raise PermissionError("provider secret file must not be a symlink")
        if not stat.S_ISREG(details.st_mode):
            raise PermissionError("provider secret path must be a regular file")
        if os.geteuid() != 0 and details.st_uid != os.getuid():
            raise PermissionError("provider secret file must be owned by the current user")
        if stat.S_IMODE(details.st_mode) != 0o600:
            raise PermissionError("provider secret file permissions must be 0600")

    def _read_all(self) -> dict[str, ProviderCredential]:
        self._validate_existing_file()
        if not self.path.exists():
            return {}
        if self.path.stat().st_size == 0:
            return {}
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            payload = tomllib.load(handle)
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported provider secret schema version")
        credentials: dict[str, ProviderCredential] = {}
        for item in payload.get("profiles", []):
            if not isinstance(item, dict):
                raise ValueError("invalid provider secret profile")
            values = dict(item)
            api_key = values.pop("api_key", None)
            if not isinstance(api_key, str):
                raise ValueError("provider API key is missing")
            profile = ProviderProfile.model_validate(values)
            if profile.profile_id in credentials:
                raise ValueError("duplicate provider profile in secret file")
            credentials[profile.profile_id] = ProviderCredential(profile, api_key)
        return credentials

    @staticmethod
    def _serialize(credentials: dict[str, ProviderCredential]) -> str:
        lines = ["schema_version = 1", ""]
        for profile_id in sorted(credentials):
            credential = credentials[profile_id]
            values = credential.profile.model_dump(mode="json")
            lines.append("[[profiles]]")
            for name in (
                "schema_version",
                "profile_id",
                "role",
                "kind",
                "base_url",
                "model",
                "credential_version",
                "created_at",
                "updated_at",
                "fingerprint",
            ):
                lines.append(f"{name} = {json.dumps(values[name])}")
            lines.append(f"api_key = {json.dumps(credential.api_key)}")
            lines.append("")
        return "\n".join(lines)

    def _write_all(self, credentials: dict[str, ProviderCredential]) -> None:
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._validate_existing_file()
        existing_owner = None
        if self.path.exists():
            details = self.path.stat()
            existing_owner = (details.st_uid, details.st_gid)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=parent
        )
        try:
            if os.geteuid() == 0 and existing_owner is not None:
                os.fchown(descriptor, *existing_owner)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(self._serialize(credentials))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
            directory = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def upsert(
        self,
        profile: ProviderProfile,
        api_key: str,
        *,
        replace: bool = True,
    ) -> None:
        credentials = self._read_all()
        if not replace and profile.profile_id in credentials:
            raise ValueError("provider profile already exists; pass --replace to update it")
        credentials[profile.profile_id] = ProviderCredential(
            profile=profile,
            api_key=validate_provider_api_key(api_key),
        )
        self._write_all(credentials)

    def get(self, profile_id: str) -> ProviderCredential:
        try:
            return self._read_all()[profile_id]
        except KeyError as error:
            raise KeyError(f"unknown provider secret profile: {profile_id}") from error

    def list_profiles(self) -> list[ProviderProfile]:
        return [item.profile for item in self._read_all().values()]

    def remove(self, profile_id: str) -> None:
        credentials = self._read_all()
        if profile_id not in credentials:
            raise KeyError(f"unknown provider secret profile: {profile_id}")
        del credentials[profile_id]
        self._write_all(credentials)
