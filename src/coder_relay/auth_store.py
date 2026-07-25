from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .errors import InvalidProfileError
from .storage import Paths, atomic_write

AuthSource = Literal["file", "keyring"]
KEYRING_SERVICE = "Codex Auth"
VALID_SOURCE_OVERRIDES = {"auto", "file", "keyring"}
VALID_STORE_MODES = {"file", "keyring", "auto", "ephemeral"}


@dataclass(frozen=True, slots=True)
class ActiveAuthSnapshot:
    raw: bytes
    source: AuthSource
    configured_mode: str
    expired: bool = False


def _config_document(path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InvalidProfileError(f"Missing Codex config: {path}") from exc
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise InvalidProfileError(f"Invalid Codex config: {path}: {exc}") from exc
    return data


def configured_store_mode(path: Path) -> tuple[str, bool]:
    """Return the configured credential store and whether it was explicit."""
    data = _config_document(path)
    value = data.get("cli_auth_credentials_store")
    if value is None:
        return "file", False
    if not isinstance(value, str) or value not in VALID_STORE_MODES:
        raise InvalidProfileError(
            "cli_auth_credentials_store must be file, keyring, auto, or ephemeral."
        )
    return value, True


def config_looks_like_api(path: Path) -> bool:
    provider = _config_document(path).get("model_provider")
    return isinstance(provider, str) and provider not in {"", "openai"}


def compute_keyring_account(codex_home: Path) -> str:
    canonical = codex_home.expanduser().resolve(strict=False)
    digest = hashlib.sha256(str(canonical).encode()).hexdigest()[:16]
    return f"cli|{digest}"


def _security_command() -> str | None:
    if sys.platform != "darwin":
        return None
    return shutil.which("security") or ("/usr/bin/security" if Path("/usr/bin/security").exists() else None)


def _load_macos_keyring(codex_home: Path) -> bytes | None:
    security = _security_command()
    if not security:
        return None
    account = compute_keyring_account(codex_home)
    result = subprocess.run(
        [security, "find-generic-password", "-s", KEYRING_SERVICE, "-a", account, "-w"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().lower()
        if "could not be found" in detail or "item not found" in detail:
            return None
        raise InvalidProfileError(
            (result.stderr or result.stdout).strip() or "Could not read Codex credentials from macOS Keychain."
        )
    raw = result.stdout.encode()
    _validate_auth_bytes(raw, "macOS Keychain")
    return raw


def _save_macos_keyring(codex_home: Path, raw: bytes) -> None:
    security = _security_command()
    if not security:
        raise InvalidProfileError("Codex keyring switching is currently supported on macOS only.")
    _validate_auth_bytes(raw, "profile auth")
    try:
        serialized = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise InvalidProfileError("Profile auth is not valid UTF-8 JSON.") from exc
    account = compute_keyring_account(codex_home)
    result = subprocess.run(
        [
            security,
            "add-generic-password",
            "-U",
            "-s",
            KEYRING_SERVICE,
            "-a",
            account,
            "-w",
            serialized,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise InvalidProfileError(
            (result.stderr or result.stdout).strip() or "Could not write Codex credentials to macOS Keychain."
        )


def _delete_macos_keyring(codex_home: Path) -> None:
    security = _security_command()
    if not security:
        return
    subprocess.run(
        [
            security,
            "delete-generic-password",
            "-s",
            KEYRING_SERVICE,
            "-a",
            compute_keyring_account(codex_home),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _validate_auth_bytes(raw: bytes, source: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidProfileError(f"Invalid auth JSON from {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidProfileError(f"Auth data from {source} must be a JSON object.")
    return payload


def _jwt_payload(token: Any) -> dict[str, Any]:
    if not isinstance(token, str):
        return {}
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        value = json.loads(base64.urlsafe_b64decode(part))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _auth_recency(raw: bytes) -> float:
    payload = _validate_auth_bytes(raw, "credential store")
    candidates: list[float] = []
    last_refresh = payload.get("last_refresh")
    if isinstance(last_refresh, str):
        try:
            candidates.append(datetime.fromisoformat(last_refresh.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        for name in ("access_token", "id_token"):
            exp = _jwt_payload(tokens.get(name)).get("exp")
            if isinstance(exp, (int, float)):
                candidates.append(float(exp))
    return max(candidates, default=0.0)


def _auth_expired(raw: bytes) -> bool:
    payload = _validate_auth_bytes(raw, "credential store")
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return False
    exp = _jwt_payload(tokens.get("access_token")).get("exp")
    return isinstance(exp, (int, float)) and float(exp) <= datetime.now(UTC).timestamp() + 60


def _file_auth(paths: Paths) -> bytes | None:
    try:
        raw = paths.active_auth.read_bytes()
    except FileNotFoundError:
        return None
    _validate_auth_bytes(raw, str(paths.active_auth))
    return raw


def load_active_auth(paths: Paths, *, source_override: str = "auto") -> ActiveAuthSnapshot:
    """Load the credential source Codex currently uses for this CODEX_HOME."""
    if source_override not in VALID_SOURCE_OVERRIDES:
        raise InvalidProfileError("--auth-source must be auto, file, or keyring.")
    configured, explicit = configured_store_mode(paths.active_config)
    if configured == "ephemeral" and source_override == "auto":
        raise InvalidProfileError("Ephemeral Codex credentials cannot be imported or switched.")

    file_raw = _file_auth(paths) if source_override in {"auto", "file"} else None
    keyring_raw = _load_macos_keyring(paths.codex_home) if source_override in {"auto", "keyring"} else None

    if source_override == "file":
        selected = (file_raw, "file")
    elif source_override == "keyring":
        selected = (keyring_raw, "keyring")
    elif configured == "keyring":
        selected = (keyring_raw, "keyring")
    elif configured == "auto":
        selected = (keyring_raw, "keyring") if keyring_raw is not None else (file_raw, "file")
    elif explicit or config_looks_like_api(paths.active_config):
        selected = (file_raw, "file")
    else:
        candidates = [(file_raw, "file"), (keyring_raw, "keyring")]
        available = [(raw, source) for raw, source in candidates if raw is not None]
        selected = max(available, key=lambda item: _auth_recency(item[0])) if available else (None, "file")

    raw, source = selected
    if raw is None:
        raise InvalidProfileError(
            "No exportable Codex credentials were found. A desktop-only session may be separate from "
            "the CLI credential store. Sign in with `codex login` in Terminal, or retry with "
            "`cdy import-current --auth-source keyring` on macOS."
        )
    return ActiveAuthSnapshot(
        raw=raw,
        source=source,  # type: ignore[arg-type]
        configured_mode=configured,
        expired=_auth_expired(raw),
    )


def write_active_auth(paths: Paths, raw: bytes, *, source: AuthSource) -> None:
    _validate_auth_bytes(raw, "profile auth")
    if source == "keyring":
        _save_macos_keyring(paths.codex_home, raw)
        paths.active_auth.unlink(missing_ok=True)
        return
    atomic_write(paths.active_auth, raw, 0o600)


def clear_active_auth(paths: Paths, *, source: AuthSource) -> None:
    if source == "keyring":
        _delete_macos_keyring(paths.codex_home)
    else:
        paths.active_auth.unlink(missing_ok=True)
