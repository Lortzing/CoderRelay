from __future__ import annotations

import sys
from typing import Annotated

import typer
from typer._completion_classes import completion_init

from . import cli as base_cli
from .completion import ensure_completion
from .lifecycle import cleanup_relay, uninstall_and_exit, update_and_exit
from .runtime_manager import RuntimeRelayManager

# The public CLI callback resolves RelayManager from the cli module at runtime.
# Replace it before any command runs so status, switching, failover, and bootstrap
# all use account-aware credential-store semantics.
base_cli.RelayManager = RuntimeRelayManager

app = base_cli.app
console = base_cli.console

# Remove commands whose public behavior is replaced by this module.
app.registered_commands[:] = [
    command
    for command in app.registered_commands
    if command.name not in {"list", "import-current", "uninstall"}
]


@app.command("import-current")
def import_current(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Profile name. Omit it to synchronize an existing matching account."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Replace a differently identified named profile."),
    ] = False,
    auth_source: Annotated[
        str,
        typer.Option(
            "--auth-source",
            help="Credential source: auto, file, or keyring. Keyring import is supported on macOS.",
        ),
    ] = "auto",
    health_mode: Annotated[
        str | None,
        typer.Option("--health-mode", help="API probe mode override: responses or models."),
    ] = None,
    balance_url: Annotated[str | None, typer.Option("--balance-url")] = None,
    balance_path: Annotated[str | None, typer.Option("--balance-path")] = None,
    notes: Annotated[str | None, typer.Option("--notes")] = None,
) -> None:
    """Import or synchronize the account used by the active Codex CLI configuration."""
    manager: RuntimeRelayManager = base_cli._manager(ctx)
    try:
        profile = manager.import_current_profile(
            name,
            force=force,
            auth_source=auth_source,
            health_mode=health_mode,
            balance_url=balance_url,
            balance_path=balance_path,
            notes=notes,
        )
    except Exception as exc:
        base_cli._fail(exc)
    detail = profile.account_email or profile.base_url or profile.provider_id or profile.kind
    console.print(
        f"Imported or synchronized [bold green]{profile.name}[/bold green] "
        f"([cyan]{profile.kind}[/cyan], {detail}, auth={profile.auth_source})."
    )
    console.print("Repeated imports of the same account update this profile instead of creating a copy.")


@app.command("update")
def update(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Reinstall the latest stable release even if versions match."),
    ] = False,
) -> None:
    """Download, verify, and install the latest stable GitHub Release."""
    if not yes and not typer.confirm(
        "Download and install the latest stable CoderRelay release?",
        default=True,
    ):
        console.print("Update cancelled.")
        raise typer.Exit(0)
    console.print("Checking the latest release and verifying its assets...")
    console.file.flush()
    update_and_exit(force=force)


@app.command("uninstall")
def uninstall(
    ctx: typer.Context,
    purge: Annotated[
        bool,
        typer.Option(
            "--purge",
            help="Delete profiles, backups, state, and cached metadata during uninstall.",
        ),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Uninstall CoderRelay and choose whether managed profile data is preserved."""
    manager = base_cli._manager(ctx)
    should_purge = purge

    if purge:
        if not yes and not typer.confirm(
            "Permanently delete all CoderRelay profiles, backups, and state?",
            default=False,
        ):
            console.print("Uninstall cancelled.")
            raise typer.Exit(0)
    elif not yes:
        keep_data = typer.confirm(
            "Preserve CoderRelay profile data, backups, and state?",
            default=True,
        )
        should_purge = not keep_data
        if should_purge and not typer.confirm(
            "Profile data will be permanently deleted. Continue?",
            default=False,
        ):
            console.print("Uninstall cancelled.")
            raise typer.Exit(0)

    result = cleanup_relay(app_home=manager.paths.app_home, purge=should_purge)
    console.print(f"Removed {result.completion_files_removed} completion artifact(s).")
    if result.data_removed:
        console.print("Managed profiles, backups, and state were deleted.")
    else:
        console.print(f"Managed profile data was preserved at {manager.paths.app_home}.")
    console.print("Active Codex configuration and credential stores were not removed.")
    console.print("Removing the installed package...")
    console.file.flush()
    uninstall_and_exit()


def main() -> None:
    """Run the public CoderRelay CLI."""
    completion_init()
    if "uninstall" not in sys.argv[1:]:
        ensure_completion(app)
    app()
