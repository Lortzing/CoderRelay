from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.live import Live
from rich.table import Table
from typer._completion_classes import completion_init

from . import __version__
from . import cli as base_cli
from .completion import ensure_completion
from .lifecycle import cleanup_relay, uninstall_and_exit, update_and_exit
from .models import ProbeResult, Profile
from .runtime_manager import RuntimeRelayManager

# The public CLI callback resolves RelayManager from the cli module at runtime.
# Replace it before any command runs so status, switching, failover, and bootstrap
# all use account-aware credential-store semantics.
base_cli.RelayManager = RuntimeRelayManager

app = base_cli.app
console = base_cli.console
_base_callback = base_cli.callback


def _version_callback(value: bool) -> bool:
    if value:
        typer.echo(f"CoderRelay {__version__}")
        raise typer.Exit()
    return value


@app.callback()
def callback(
    ctx: typer.Context,
    home: Annotated[
        Path | None,
        typer.Option("--home", help="CoderRelay data directory."),
    ] = None,
    codex_home: Annotated[
        Path | None,
        typer.Option("--codex-home", help="Codex configuration directory."),
    ] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed CoderRelay version and exit.",
        ),
    ] = False,
) -> None:
    """Initialize CoderRelay paths and expose global CLI options."""
    _base_callback(ctx, home=home, codex_home=codex_home)


# Remove commands whose public behavior is replaced by this module.
app.registered_commands[:] = [
    command
    for command in app.registered_commands
    if command.name not in {"list", "import-current", "status", "uninstall"}
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
    if not manager.last_import_activated:
        console.print(
            "The selected credential source is not active in the current CLI config. "
            f"Run [bold]cdy use {profile.name}[/bold] to activate it."
        )


def _status_table(
    profiles: list[Profile],
    active: str | None,
    results: list[ProbeResult] | None,
    active_state: str,
    *,
    show_detail: bool,
) -> Table:
    result_map = {item.profile: item for item in (results or [])}
    table = Table(title=f"Codex profiles and status · active state: {active_state}")
    table.add_column("Active", justify="center")
    table.add_column("Profile", style="bold")
    table.add_column("Type")
    table.add_column("Model")
    table.add_column("Endpoint / account", overflow="fold")
    table.add_column("Check")
    table.add_column("Health")
    table.add_column("Latency")
    table.add_column("Usage / balance")
    if show_detail:
        table.add_column("Detail", overflow="fold")

    for profile in profiles:
        result = result_map.get(profile.name)
        endpoint = profile.base_url or (profile.account_email or "ChatGPT")
        if result is None:
            health = "[dim]not checked[/dim]"
            latency = "—"
            usage = "—"
            detail = "network probe disabled"
        else:
            health = "[green]healthy[/green]" if result.healthy else f"[red]{result.status}[/red]"
            latency = f"{result.latency_ms:.0f} ms" if result.latency_ms is not None else "—"
            usage = base_cli._usage_text(result)
            detail = result.message or ""

        row = [
            "●" if profile.name == active else "",
            profile.name,
            profile.kind,
            profile.model or "—",
            endpoint,
            profile.health.mode,
            health,
            latency,
            usage,
        ]
        if show_detail:
            row.append(detail)
        table.add_row(*row)
    return table


@app.command("status")
def status(
    ctx: typer.Context,
    no_probe: Annotated[
        bool,
        typer.Option("--no-probe", help="Do not make network requests."),
    ] = False,
    watch: Annotated[
        bool,
        typer.Option("--watch", help="Continuously refresh status."),
    ] = False,
    interval: Annotated[float, typer.Option("--interval", min=1.0)] = 30.0,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    detail: Annotated[
        bool,
        typer.Option("--detail", help="Show compact probe details in the human-readable table."),
    ] = False,
) -> None:
    """List profiles and show active state, health, usage, and optional API balance."""
    manager = base_cli._manager(ctx)

    def render_once() -> tuple[Table, dict[str, Any]]:
        profiles, active, active_state, results = base_cli._status_snapshot(manager, not no_probe)
        payload = {
            "active_profile": active,
            "active_state": active_state,
            "profiles": [profile.to_dict() for profile in profiles],
            "results": [result.to_dict() for result in (results or [])],
        }
        return (
            _status_table(
                profiles,
                active,
                results,
                active_state,
                show_detail=detail,
            ),
            payload,
        )

    if not watch:
        table, payload = render_once()
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            console.print(table)
            app_context = ctx.obj
            if app_context.bootstrap_error and not payload["profiles"]:
                console.print(
                    "[yellow]Current Codex configuration was not auto-imported:[/yellow] "
                    + app_context.bootstrap_error
                )
        return

    if json_output:
        try:
            while True:
                _, payload = render_once()
                typer.echo(json.dumps(payload, ensure_ascii=False))
                time.sleep(interval)
        except KeyboardInterrupt:
            return

    table, _ = render_once()
    try:
        with Live(table, console=console, refresh_per_second=4) as live:
            while True:
                time.sleep(interval)
                table, _ = render_once()
                live.update(table)
    except KeyboardInterrupt:
        return


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
    if not any(arg in {"uninstall", "--version", "-V"} for arg in sys.argv[1:]):
        ensure_completion(app)
    app()
