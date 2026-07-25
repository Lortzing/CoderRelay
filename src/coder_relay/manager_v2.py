from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Iterable

import tomlkit

from .auth_store import ActiveAuthSnapshot, load_active_auth, write_active_auth
from .config import inspect_codex_config, validate_toml
from .errors import InvalidProfileError, ProfileNotFoundError, SwitchError
from .manager import RelayManager
from .models import BalanceConfig, HealthConfig, ProbeResult, Profile, utc_now_iso
from .storage import FileLock, atomic_write, backup_active, validate_name, write_json
from .usage import parse_auth_bytes, parse_auth_json


def _auth_identity(auth_info: dict[str, Any]) -> str:
    if auth_info["kind"] == "chatgpt":
        account_id = auth_info.get("account_id")
        if isinstance(account_id, str) and account_id:
            return f"chatgpt:{account_id}"
        user_id = auth_info.get("user_id")
        if isinstance(user_id, str) and user_id:
            return f"chatgpt-user:{user_id}"
        email = auth_info.get("email")
        if isinstance(email, str) and email:
            return f"chatgpt-email:{email.lower()}"
        raise InvalidProfileError("Could not determine the ChatGPT account identity.")
    api_key = str(auth_info.get("api_key") or "")
    if not api_key:
        raise InvalidProfileError("Could not determine the API credential identity.")
    return "api:" + hashlib.sha256(api_key.encode()).hexdigest()


def _config_for_source(content: bytes, source: str) -> bytes:
    try:
        document = tomlkit.parse(content.decode("utf-8"))
    except (UnicodeDecodeError, Exception) as exc:
        raise InvalidProfileError(f"Invalid Codex config while selecting auth storage: {exc}") from exc
    document["cli_auth_credentials_store"] = source
    return tomlkit.dumps(document).encode("utf-8")


class EnhancedRelayManager(RelayManager):
    """Relay manager with account identity, credential-store, and sync semantics."""

    def bootstrap_current_profile(self) -> Profile | None:
        if self.list_profiles() or not self.paths.active_config.is_file():
            return None
        try:
            load_active_auth(self.paths)
        except InvalidProfileError:
            return None
        with FileLock(self.paths.lock_file):
            if self.list_profiles():
                return None
            return self.import_current_profile()

    def _profile_auth_info(self, name: str) -> dict[str, Any]:
        return parse_auth_json(self._profile_paths(name)[2])

    def _profile_identity(self, name: str) -> str:
        return _auth_identity(self._profile_auth_info(name))

    def _matching_profiles(self, identity: str) -> list[Profile]:
        matches: list[Profile] = []
        for profile in self.list_profiles():
            try:
                if self._profile_identity(profile.name) == identity:
                    matches.append(profile)
            except InvalidProfileError:
                continue
        return matches

    def _build_profile(
        self,
        name: str,
        auth_info: dict[str, Any],
        config_info: dict[str, str | None],
        *,
        auth_source: str,
        existing: Profile | None = None,
        health_mode: str | None = None,
        balance_url: str | None = None,
        balance_path: str | None = None,
        notes: str | None = None,
    ) -> Profile:
        now = utc_now_iso()
        if auth_info["kind"] == "chatgpt":
            return Profile(
                name=name,
                kind="chatgpt",
                created_at=existing.created_at if existing else now,
                updated_at=now,
                model=config_info.get("model"),
                account_email=auth_info.get("email"),
                account_plan=auth_info.get("plan"),
                account_id=auth_info.get("account_id"),
                account_user_id=auth_info.get("user_id"),
                auth_source=auth_source,  # type: ignore[arg-type]
                health=existing.health if existing else HealthConfig(mode="chatgpt_usage"),
                balance=existing.balance if existing else None,
                notes=notes if notes is not None else (existing.notes if existing else "Imported from the active Codex configuration."),
            )

        resolved_health_mode = health_mode or (existing.health.mode if existing else None) or (
            "responses" if config_info.get("model") else "models"
        )
        if resolved_health_mode not in {"responses", "models"}:
            raise InvalidProfileError("Imported API profiles support responses or models health mode.")
        balance = existing.balance if existing else None
        if balance_url:
            balance = BalanceConfig(balance_url, balance_path)
        return Profile(
            name=name,
            kind="api",
            created_at=existing.created_at if existing else now,
            updated_at=now,
            model=config_info.get("model"),
            base_url=config_info.get("base_url"),
            provider_id=config_info.get("provider_id"),
            auth_source="file",
            health=existing.health if existing else HealthConfig(mode=resolved_health_mode),  # type: ignore[arg-type]
            balance=balance,
            notes=notes if notes is not None else (existing.notes if existing else "Imported from the active Codex configuration."),
        )

    def _write_profile_snapshot(
        self,
        profile: Profile,
        auth_raw: bytes,
        config_raw: bytes,
    ) -> Profile:
        directory, metadata_path, stored_auth, stored_config = self._profile_paths(profile.name)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        normalized_config = _config_for_source(config_raw, profile.auth_source)
        atomic_write(stored_auth, auth_raw, 0o600)
        atomic_write(stored_config, normalized_config, 0o600)
        write_json(metadata_path, profile.to_dict(), 0o600)
        return profile

    def import_current_profile(
        self,
        name: str | None = None,
        *,
        force: bool = False,
        health_mode: str | None = None,
        balance_url: str | None = None,
        balance_path: str | None = None,
        notes: str | None = None,
        auth_source: str = "auto",
    ) -> Profile:
        """Import or synchronize the active Codex account without creating duplicates."""
        snapshot = load_active_auth(self.paths, source_override=auth_source)
        auth_info = parse_auth_bytes(snapshot.raw, source=f"active {snapshot.source} credentials")
        if snapshot.expired and auth_info["kind"] == "chatgpt":
            raise InvalidProfileError(
                "The exportable ChatGPT token is expired. The desktop app may be using a separate "
                "session. Run `codex login` in Terminal for the intended account, then import again."
            )
        config_info = inspect_codex_config(self.paths.active_config, auth_kind=str(auth_info["kind"]))
        identity = _auth_identity(auth_info)
        matches = self._matching_profiles(identity)
        state = self._state()

        if name is None:
            selected = next((item for item in matches if item.name == state.active_profile), None)
            selected = selected or (sorted(matches, key=lambda item: item.name.lower())[0] if matches else None)
            if selected:
                resolved_name = selected.name
                existing = selected
            else:
                if auth_info["kind"] == "chatgpt":
                    email = auth_info.get("email")
                    suggested = email.split("@", 1)[0] if isinstance(email, str) and email else "chatgpt"
                else:
                    provider = config_info.get("provider_id")
                    suggested = "openai-api" if provider == "openai" else str(provider or "api")
                resolved_name = self._unique_import_name(suggested)
                existing = None
        else:
            validate_name(name)
            resolved_name = name
            try:
                existing = self.load_profile(name)
            except ProfileNotFoundError:
                existing = None
            if existing and self._profile_identity(name) != identity and not force:
                raise InvalidProfileError(
                    f"Profile {name} belongs to a different account. Use --force only to replace it intentionally."
                )
            other_match = next((item for item in matches if item.name != name), None)
            if existing is None and other_match and not force:
                raise InvalidProfileError(
                    f"This account already exists as {other_match.name}; omit the name to synchronize it."
                )

        profile = self._build_profile(
            resolved_name,
            auth_info,
            config_info,
            auth_source=snapshot.source,
            existing=existing,
            health_mode=health_mode,
            balance_url=balance_url,
            balance_path=balance_path,
            notes=notes,
        )
        with FileLock(self.paths.lock_file):
            if existing is not None and force and self._profile_identity(existing.name) != identity:
                shutil.rmtree(self._profile_paths(existing.name)[0], ignore_errors=True)
            self._write_profile_snapshot(
                profile,
                snapshot.raw,
                self.paths.active_config.read_bytes(),
            )
            state = self._state()
            state.active_profile = profile.name
            state.last_switch_at = utc_now_iso()
            state.last_switch_reason = "synchronized active Codex configuration"
            self._save_state(state)
        return profile

    def _sync_active_profile(self) -> Profile | None:
        if not self.paths.active_config.is_file():
            return None
        try:
            snapshot = load_active_auth(self.paths)
            auth_info = parse_auth_bytes(snapshot.raw, source=f"active {snapshot.source} credentials")
            config_info = inspect_codex_config(self.paths.active_config, auth_kind=str(auth_info["kind"]))
            identity = _auth_identity(auth_info)
        except (InvalidProfileError, OSError):
            return None

        state = self._state()
        candidates = self._matching_profiles(identity)
        selected = next((item for item in candidates if item.name == state.active_profile), None)
        selected = selected or (sorted(candidates, key=lambda item: item.name.lower())[0] if candidates else None)
        if selected is None:
            return None

        refreshed = self._build_profile(
            selected.name,
            auth_info,
            config_info,
            auth_source=snapshot.source,
            existing=selected,
        )
        self._write_profile_snapshot(
            refreshed,
            snapshot.raw,
            self.paths.active_config.read_bytes(),
        )
        if state.active_profile != refreshed.name:
            state.active_profile = refreshed.name
            state.last_switch_reason = "matched active Codex account identity"
            self._save_state(state)
        return refreshed

    def current_profile(self) -> tuple[str | None, str]:
        synchronized = self._sync_active_profile()
        if synchronized is not None:
            return synchronized.name, "managed"
        return super().current_profile()

    def switch(self, name: str, *, reason: str = "manual") -> Path | None:
        target = self.load_profile(name)
        _, _, source_auth, source_config = self._profile_paths(name)
        target_auth = source_auth.read_bytes()
        target_config = source_config.read_bytes()
        parse_auth_bytes(target_auth, source=f"profile {name}")
        validate_toml(target_config.decode("utf-8"))

        # Preserve refreshed OAuth tokens and any current config edits before leaving
        # the active account, matching the useful behavior of cswitch snapshots.
        self._sync_active_profile()
        try:
            old_auth: ActiveAuthSnapshot | None = load_active_auth(self.paths)
        except InvalidProfileError:
            old_auth = None
        old_config = self.paths.active_config.read_bytes() if self.paths.active_config.exists() else None

        with FileLock(self.paths.lock_file):
            backup = backup_active(self.paths)
            if backup is not None and old_auth is not None:
                atomic_write(backup / "auth.json", old_auth.raw, 0o600)
            try:
                write_active_auth(self.paths, target_auth, source=target.auth_source)
                atomic_write(self.paths.active_config, target_config, 0o600)
                loaded = load_active_auth(self.paths, source_override=target.auth_source)
                parse_auth_bytes(loaded.raw, source=f"activated profile {name}")
                validate_toml(self.paths.active_config.read_text(encoding="utf-8"))
                state = self._state()
                state.active_profile = name
                state.last_switch_at = utc_now_iso()
                state.last_switch_reason = reason
                self._save_state(state)
                return backup
            except Exception as exc:
                try:
                    if old_config is None:
                        self.paths.active_config.unlink(missing_ok=True)
                    else:
                        atomic_write(self.paths.active_config, old_config, 0o600)
                    if old_auth is not None:
                        write_active_auth(self.paths, old_auth.raw, source=old_auth.source)
                except OSError:
                    pass
                raise SwitchError(f"Failed to switch to {name}; active credentials were rolled back: {exc}") from exc

    def probe_many(self, names: Iterable[str] | None = None, *, workers: int = 4) -> list[ProbeResult]:
        self._sync_active_profile()
        return super().probe_many(names, workers=workers)

    def add_auth_profile(self, *args, **kwargs) -> Profile:
        profile = super().add_auth_profile(*args, **kwargs)
        auth_info = self._profile_auth_info(profile.name)
        profile.account_id = auth_info.get("account_id")
        profile.account_user_id = auth_info.get("user_id")
        profile.auth_source = "file"
        profile.schema_version = 2
        write_json(self._profile_paths(profile.name)[1], profile.to_dict(), 0o600)
        return profile

    def add_api_profile(self, *args, **kwargs) -> Profile:
        profile = super().add_api_profile(*args, **kwargs)
        profile.auth_source = "file"
        profile.schema_version = 2
        write_json(self._profile_paths(profile.name)[1], profile.to_dict(), 0o600)
        return profile
