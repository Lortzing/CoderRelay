# CoderRelay

[简体中文](README.md) | English

[![CI](https://github.com/Lortzing/CoderRelay/actions/workflows/ci.yml/badge.svg)](https://github.com/Lortzing/CoderRelay/actions/workflows/ci.yml)
[![GitHub Release](https://img.shields.io/github/v/release/Lortzing/CoderRelay)](https://github.com/Lortzing/CoderRelay/releases)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/github/license/Lortzing/CoderRelay)](LICENSE)

CoderRelay manages accounts, profiles, and API routes for coding-agent CLIs. The current release supports OpenAI Codex CLI; Claude Code support is planned.

Key capabilities:

- multiple ChatGPT/Codex and API-key profiles;
- identity-based, idempotent `import-current` synchronization;
- preservation of OAuth credentials refreshed by Codex before switching;
- Codex `auth.json` and macOS Keychain credential sources;
- health checks, automatic failover, and preferred-profile recovery;
- verified automatic updates from stable GitHub Releases.

## Installation

Download the matching Windows Setup/ZIP, macOS DMG, or Linux TAR/DEB/RPM asset from the release. The macOS package installs its persistent runtime under `/usr/local/lib/coder-relay/` and exposes `/usr/local/bin/cdy`.

Source installation:

```bash
git clone https://github.com/Lortzing/CoderRelay.git
cd CoderRelay
./install.sh
```

Fixed tag:

```bash
uv tool install --force git+https://github.com/Lortzing/CoderRelay.git@v0.8.1
```

## Quick start

```bash
cdy status
cdy status --no-probe
cdy import-current
cdy use official
cdy auto official backup --watch
cdy launch -p official -p backup --
```

Create an API profile:

```bash
cdy add-api backup \
  --url https://gateway.example.com/v1 \
  --model gpt-5.6 \
  --api-key-stdin
```

## Account import

Starting with v0.8.1, repeated imports use the stable ChatGPT account ID or an API credential hash. The same account is synchronized in place instead of producing `name-2`, `name-3`, and similar copies.

```bash
cdy import-current
```

Codex CLI credentials can be stored in `$CODEX_HOME/auth.json` or, on macOS, in the Codex Keychain entry. A source can be selected explicitly:

```bash
cdy import-current --auth-source file
cdy import-current --auth-source keyring
```

Importing an alternate Keychain account while the active CLI config still uses an API profile saves the profile without falsely marking it active. Activate it explicitly:

```bash
cdy use <profile>
```

A session that exists only inside a desktop application and is not exported to the CLI file or Keychain store cannot be copied safely. Sign in to the same account with `codex login` in Terminal first.

See [docs/auth-and-switching.md](docs/auth-and-switching.md).

## Switching

Before switching away, CoderRelay saves refreshed active credentials and config into the matching profile, then backs up and activates the target profile.

Generated API profiles set:

```toml
cli_auth_credentials_store = "file"
```

This prevents an unrelated desktop or Keychain ChatGPT login from overriding the selected API key.

Restart existing Codex CLI or desktop processes after switching so they reload credentials and config.

## Health checks

A Responses API probe is healthy only when it returns successful JSON containing output text. HTTP 200 HTML challenge pages are reported as `invalid_response`. Verbose error bodies are omitted from the human-readable status table.

## Storage

```text
~/.config/coder-relay/
├── profiles/
├── backups/
├── state.json
└── switch.lock
```

Existing duplicates are not deleted automatically because the same account may intentionally have different configs. Remove only confirmed unwanted profiles:

```bash
cdy remove <profile>
```

## Updates and uninstall

```bash
cdy update
cdy update -y
cdy update --force
cdy uninstall
cdy uninstall --purge
```

The updater downloads the matching release asset and `SHA256SUMS.txt`, verifies SHA-256, and then invokes the native platform installer or replacement mechanism.

## Release

```bash
git tag -a v0.8.1 -m "CoderRelay v0.8.1"
git push origin v0.8.1
```

Release artifacts are unsigned.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run cdy --help
uv build --no-sources
```

## License

MIT
