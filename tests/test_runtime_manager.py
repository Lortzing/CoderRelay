import json
from pathlib import Path

from coder_relay import auth_store
from coder_relay.runtime_manager import RuntimeRelayManager


def _write_active(paths, auth: bytes, config: str) -> None:
    paths.codex_home.mkdir(parents=True, exist_ok=True)
    paths.active_auth.write_bytes(auth)
    paths.active_config.write_text(config, encoding="utf-8")


def test_repeated_import_synchronizes_same_account(paths, chatgpt_auth: Path) -> None:
    manager = RuntimeRelayManager(paths)
    config = 'model = "gpt-5.6"\nmodel_provider = "openai"\n'
    _write_active(paths, chatgpt_auth.read_bytes(), config)

    first = manager.import_current_profile()
    changed = json.loads(chatgpt_auth.read_text(encoding="utf-8"))
    changed["tokens"]["access_token"] = "refreshed-access-token"
    paths.active_auth.write_text(json.dumps(changed), encoding="utf-8")
    second = manager.import_current_profile()

    assert first.name == "test"
    assert second.name == "test"
    assert [profile.name for profile in manager.list_profiles()] == ["test"]
    stored = json.loads(manager.profile_auth_path("test").read_text(encoding="utf-8"))
    assert stored["tokens"]["access_token"] == "refreshed-access-token"


def test_switch_saves_refreshed_active_credentials(paths, chatgpt_auth: Path) -> None:
    manager = RuntimeRelayManager(paths)
    paths.active_config.write_text('model_provider = "openai"\n', encoding="utf-8")
    manager.add_auth_profile("official", chatgpt_auth)
    manager.add_api_profile(
        "backup",
        base_url="https://api.example/v1",
        api_key="secret",
        model="gpt-test",
    )
    manager.switch("official")

    active = json.loads(paths.active_auth.read_text(encoding="utf-8"))
    active["tokens"]["access_token"] = "new-token-from-codex"
    paths.active_auth.write_text(json.dumps(active), encoding="utf-8")
    manager.switch("backup")

    saved = json.loads(manager.profile_auth_path("official").read_text(encoding="utf-8"))
    assert saved["tokens"]["access_token"] == "new-token-from-codex"
    assert manager.current_profile() == ("backup", "managed")


def test_keyring_import_uses_account_identity(paths, chatgpt_auth: Path, monkeypatch) -> None:
    manager = RuntimeRelayManager(paths)
    paths.active_config.write_text(
        'model = "gpt-5.6"\nmodel_provider = "openai"\ncli_auth_credentials_store = "keyring"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(auth_store, "_load_macos_keyring", lambda codex_home: chatgpt_auth.read_bytes())

    first = manager.import_current_profile(auth_source="keyring")
    second = manager.import_current_profile(auth_source="keyring")

    assert first.name == second.name == "test"
    assert first.auth_source == "keyring"
    assert manager.last_import_activated is True
    assert len(manager.list_profiles()) == 1
    stored_config = manager._profile_paths("test")[3].read_text(encoding="utf-8")
    assert 'cli_auth_credentials_store = "keyring"' in stored_config
    assert 'model_provider = "openai"' in stored_config


def test_alternate_keyring_import_does_not_replace_active_api_state(
    paths, chatgpt_auth: Path, monkeypatch
) -> None:
    manager = RuntimeRelayManager(paths)
    manager.add_api_profile(
        "gateway",
        base_url="https://gateway.example/v1",
        api_key="api-secret",
        model="gpt-test",
    )
    manager.switch("gateway")
    monkeypatch.setattr(auth_store, "_load_macos_keyring", lambda codex_home: chatgpt_auth.read_bytes())

    imported = manager.import_current_profile("desktop-account", auth_source="keyring")

    assert imported.kind == "chatgpt"
    assert manager.last_import_activated is False
    assert manager.current_profile() == ("gateway", "managed")


def test_generated_api_profile_forces_file_credentials(paths) -> None:
    manager = RuntimeRelayManager(paths)
    paths.active_config.write_text(
        'model_provider = "openai"\ncli_auth_credentials_store = "keyring"\n',
        encoding="utf-8",
    )
    manager.add_api_profile(
        "gateway",
        base_url="https://gateway.example/v1",
        api_key="secret",
        model="gpt-test",
    )

    config = manager._profile_paths("gateway")[3].read_text(encoding="utf-8")
    assert 'cli_auth_credentials_store = "file"' in config
