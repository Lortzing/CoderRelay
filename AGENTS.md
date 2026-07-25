# Project Agent Rules

- Work from the project root and read this file before changes.
- Use the project-local `uv` environment for Python commands.
- Keep credentials out of source control and logs.
- After code changes, run the full test suite and update this file when architecture or commands change.

## Identity

- Product: `CoderRelay`.
- Distribution and full command: `coder-relay`.
- Recommended command: `cdy`.
- Python module: `coder_relay`.
- Application home: `~/.config/coder-relay` or `$CODER_RELAY_HOME`.
- Current backend: OpenAI Codex CLI. Claude Code support is planned but not yet implemented.
- Do not reintroduce `CodexRelay`, `codex-relay`, `codex_relay`, or `cxr` compatibility aliases.

## Architecture

- `src/coder_relay/cli.py`: Typer/Rich command definitions.
- `src/coder_relay/entrypoint.py`: public command surface and lifecycle commands.
- `src/coder_relay/auth_store.py`: active Codex auth resolution across `auth.json` and the macOS `Codex Auth` Keychain entry.
- `src/coder_relay/manager.py`: legacy/base profile operations and failover implementation.
- `src/coder_relay/manager_v2.py`: account-identity deduplication, active-profile synchronization, and credential-store-aware switching.
- `src/coder_relay/runtime_manager.py`: public runtime manager and non-nested first-run bootstrap.
- `src/coder_relay/completion.py`: Bash/Zsh/Fish completion for `cdy` and `coder-relay`.
- `src/coder_relay/lifecycle.py`: update/uninstall dispatch and package-manager lifecycle behavior.
- `src/coder_relay/updater.py`: latest stable Release discovery, platform/architecture asset selection, downloads, SHA-256 verification, and native update application.
- `src/coder_relay/health.py`: ChatGPT and OpenAI-compatible API probes.
- `src/coder_relay/usage.py`: auth parsing and ChatGPT usage queries.
- `src/coder_relay/config.py`: Codex TOML generation.
- `src/coder_relay/storage.py`: paths, atomic writes, locks, and backups.
- `scripts/cdy_launcher.py`: PyInstaller entry point.
- `scripts/package_release.py`: portable archive builder.
- `scripts/build_windows_installer.ps1`: Windows Setup EXE and ZIP.
- `installers/windows/CoderRelay.iss`: Inno Setup definition.
- `scripts/build_macos_dmg.sh`: macOS PKG/DMG packaging.
- `scripts/build_linux_packages.sh`: Linux TAR/DEB/RPM packaging.

## Commands

```bash
uv sync --extra dev
uv run pytest
uv run cdy --help
uv run coder-relay --help
uv build --no-sources
```

## Account and switching policy

- Profile identity is based on the ChatGPT account ID or a hash of the API credential, never the user-facing profile name.
- Repeated `import-current` calls for the same identity synchronize the existing profile instead of creating `-2`, `-3`, or other copies.
- Before switching away, persist refreshed active credentials and config back into the matching managed profile.
- Generated API profiles must set `cli_auth_credentials_store = "file"` so an unrelated desktop/Keychain session cannot override their API key.
- On macOS, support the official direct Keychain entry with service `Codex Auth` and the CODEX_HOME-derived `cli|<hash>` account key.
- A desktop-only session that is not exported to the CLI file or Keychain store must produce a clear error; do not silently import a stale `auth.json` as the desktop account.
- Do not automatically delete existing duplicate profiles because they may intentionally carry different configs. Prevent new duplicates and let users remove old ones explicitly.

## Status output policy

- Human-readable `cdy status` output may show short health details, but probe messages longer than 60 characters must be omitted rather than wrapped or truncated across the table.
- Health status, latency, and HTTP classification remain visible when verbose details are omitted.
- A Responses API probe is healthy only when it returns a successful JSON response containing output text. HTML challenge pages must be classified as `invalid_response`.
- Never expose access tokens or API keys in status details.

## Update policy

- `cdy update` targets the latest published stable GitHub Release, never an untagged `main` snapshot.
- Python package installations update from the exact latest stable Git Tag.
- Frozen/native installations select an exact asset by operating system, architecture, and installation type.
- Every native update requires the matching `SHA256SUMS.txt` entry. When GitHub exposes a SHA-256 asset digest, verify that as well.
- A missing asset, checksum, unsupported architecture, size mismatch, or digest mismatch must fail closed without replacing the current installation.
- Windows Setup and Portable updates are applied by a detached helper after the running process exits.
- macOS updates mount the DMG and install its PKG with the system installer.
- Linux DEB/RPM updates use the installed package manager; TAR installations replace the executable atomically or with elevation.
- Updates and uninstall preserve managed data and active Codex files unless `--purge` is explicitly requested.

## Release policy

- Release tags use semantic versions prefixed with `v` and must match `pyproject.toml`.
- Native builds run on target runners.
- The Release workflow creates or reuses the GitHub Release before platform builds start.
- Each successful platform job uploads its assets directly, so one failed architecture does not block all other downloads.
- Manual Release dispatch validates the requested tag but builds platform assets from the selected workflow commit, allowing an existing failed release to be repaired from `main`.
- Windows publishes Setup EXE and ZIP for x86, x86_64, and ARM64.
- The Windows installer uses Inno Setup's built-in English messages. Additional language files must be vendored before being referenced.
- Windows installer smoke tests must use `Start-Process -Wait -PassThru`, verify the installed `cdy.exe`, exercise the uninstaller, and print Inno Setup logs on failure.
- macOS publishes DMG images containing PKG installers for Intel and Apple Silicon.
- macOS must use a PyInstaller `--onedir` runtime installed at `/usr/local/lib/coder-relay`, with `/usr/local/bin/cdy` as a symlink. Do not switch macOS back to `--onefile` because repeated extraction causes slow command startup.
- macOS packaged uninstall must remove the runtime directory, command symlink, and package receipt while preserving profile data unless `--purge` is requested.
- Linux publishes TAR.GZ, DEB, and RPM for x86_64 and AArch64.
- nFPM `v2.47.0` requires Go `1.26.4` or newer; the workflow uses Go `1.26.x`.
- `SHA256SUMS.txt` covers the assets produced by the current workflow run and reports missing targets in the job summary.
- Release artifacts are unsigned.
