from __future__ import annotations

import tomlkit

from .auth_store import load_active_auth
from .errors import InvalidProfileError
from .manager_v2 import EnhancedRelayManager
from .models import Profile
from .storage import atomic_write, write_json


class RuntimeRelayManager(EnhancedRelayManager):
    """Public runtime manager with an idempotent, non-nested bootstrap path."""

    last_import_activated: bool = True

    def bootstrap_current_profile(self) -> Profile | None:
        if self.list_profiles() or not self.paths.active_config.is_file():
            return None
        try:
            load_active_auth(self.paths)
        except InvalidProfileError:
            return None
        # import_current_profile performs its own locked write. Recheck immediately
        # before entering it, rather than nesting the same file lock.
        if self.list_profiles():
            return None
        return self.import_current_profile()

    def import_current_profile(self, *args, auth_source: str = "auto", **kwargs) -> Profile:
        previous_state = self._state()
        profile = super().import_current_profile(
            *args,
            auth_source=auth_source,
            **kwargs,
        )
        self.last_import_activated = True
        if auth_source != "auto":
            try:
                actual = load_active_auth(self.paths)
            except InvalidProfileError:
                actual = None
            if actual is None or actual.source != profile.auth_source:
                # An explicitly selected alternate source was imported, but it is
                # not what the active config currently tells Codex CLI to use.
                self._save_state(previous_state)
                self.last_import_activated = False
        return profile

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
        try:
            document = tomlkit.parse(config_raw.decode("utf-8"))
        except Exception as exc:
            raise InvalidProfileError(f"Invalid active Codex config: {exc}") from exc
        document["cli_auth_credentials_store"] = profile.auth_source
        if profile.kind == "chatgpt":
            # A ChatGPT credential imported from Keychain must not inherit an
            # API provider such as AnyRouter from the currently active CLI config.
            document["model_provider"] = "openai"
        normalized = tomlkit.dumps(document).encode("utf-8")
        atomic_write(stored_auth, auth_raw, 0o600)
        atomic_write(stored_config, normalized, 0o600)
        write_json(metadata_path, profile.to_dict(), 0o600)
        return profile
