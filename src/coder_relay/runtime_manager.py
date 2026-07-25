from __future__ import annotations

from .auth_store import load_active_auth
from .errors import InvalidProfileError
from .manager_v2 import EnhancedRelayManager
from .models import Profile


class RuntimeRelayManager(EnhancedRelayManager):
    """Public runtime manager with an idempotent, non-nested bootstrap path."""

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
