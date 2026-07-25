import json
import tomllib
from pathlib import Path

import typer
from typer.testing import CliRunner

from coder_relay import __version__
from coder_relay import entrypoint as cli
from coder_relay.entrypoint import app
from coder_relay.lifecycle import CleanupResult

runner = CliRunner()


def test_status_first_run_imports_current_profile(tmp_path: Path, chatgpt_auth: Path) -> None:
    app_home = tmp_path / "app"
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_bytes(chatgpt_auth.read_bytes())
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.6"\nmodel_provider = "openai"\n',
        encoding="utf-8",
    )

    args = [
        "--home",
        str(app_home),
        "--codex-home",
        str(codex_home),
        "status",
        "--no-probe",
        "--json",
    ]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)

    assert first.exit_code == 0, first.stdout
    assert second.exit_code == 0, second.stdout
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["active_profile"] == "test"
    assert [item["name"] for item in first_payload["profiles"]] == ["test"]
    assert [item["name"] for item in second_payload["profiles"]] == ["test"]


def test_version_option() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0, result.stdout
    assert result.stdout.strip() == f"CoderRelay {__version__}"


def test_status_hides_detail_by_default(tmp_path: Path, chatgpt_auth: Path) -> None:
    app_home = tmp_path / "app"
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_bytes(chatgpt_auth.read_bytes())
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.6"\nmodel_provider = "openai"\n',
        encoding="utf-8",
    )
    prefix = [
        "--home",
        str(app_home),
        "--codex-home",
        str(codex_home),
        "status",
        "--no-probe",
    ]

    default = runner.invoke(app, prefix, terminal_width=160)
    detailed = runner.invoke(app, [*prefix, "--detail"], terminal_width=160)

    assert default.exit_code == 0, default.stdout
    assert detailed.exit_code == 0, detailed.stdout
    assert "Detail" not in default.stdout
    assert "network probe disabled" not in default.stdout
    assert "Detail" in detailed.stdout
    assert "network probe disabled" in detailed.stdout


def test_only_current_entry_points_are_published() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"] == {
        "coder-relay": "coder_relay.entrypoint:main",
        "cdy": "coder_relay.entrypoint:main",
    }


def test_list_command_is_removed() -> None:
    result = runner.invoke(app, ["list"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def _patch_uninstall(monkeypatch, captured: dict[str, object]) -> None:
    def fake_cleanup(*, app_home: Path, purge: bool, user_home=None):
        captured["purge"] = purge
        return CleanupResult(completion_files_removed=0, data_removed=purge)

    def fake_exit() -> None:
        raise typer.Exit(0)

    monkeypatch.setattr(cli, "cleanup_relay", fake_cleanup)
    monkeypatch.setattr(cli, "uninstall_and_exit", fake_exit)


def test_uninstall_interactively_preserves_profiles(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    _patch_uninstall(monkeypatch, captured)

    result = runner.invoke(
        app,
        ["--home", str(tmp_path / "app"), "uninstall"],
        input="y\n",
    )

    assert result.exit_code == 0, result.stdout
    assert captured["purge"] is False
    assert "preserved" in result.stdout


def test_uninstall_interactively_can_delete_profiles(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    _patch_uninstall(monkeypatch, captured)

    result = runner.invoke(
        app,
        ["--home", str(tmp_path / "app"), "uninstall"],
        input="n\ny\n",
    )

    assert result.exit_code == 0, result.stdout
    assert captured["purge"] is True
    assert "deleted" in result.stdout


def test_uninstall_purge_yes_is_noninteractive(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    _patch_uninstall(monkeypatch, captured)

    result = runner.invoke(
        app,
        ["--home", str(tmp_path / "app"), "uninstall", "--purge", "--yes"],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["purge"] is True
