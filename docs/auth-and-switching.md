# Codex authentication and profile switching

CoderRelay v0.8.1 identifies profiles by account identity rather than by their display name.

## Credential sources

Codex CLI credentials may be stored in:

- `$CODEX_HOME/auth.json` (`file` mode);
- the macOS Keychain entry used by Codex (`keyring` mode);
- `auto` mode, where Codex tries Keychain and falls back to the file.

CoderRelay follows `cli_auth_credentials_store` in the active `config.toml`. On macOS, a specific source can be selected explicitly:

```bash
cdy import-current --auth-source file
cdy import-current --auth-source keyring
```

A login that exists only inside a running desktop application and is not exported to the Codex CLI file or Keychain store cannot be copied safely. Sign in to the same account with `codex login` in Terminal first.

## Idempotent import

```bash
cdy import-current
```

The command extracts the ChatGPT account ID, or hashes the API credential, and searches existing profiles for the same identity. A match is synchronized in place. Repeated imports no longer create `name-2`, `name-3`, and similar copies.

An explicitly named profile that belongs to another account is protected unless `--force` is supplied.

## Switching

Before switching away from a managed account, CoderRelay saves the active credentials and config back into that profile. This preserves OAuth tokens refreshed by Codex.

Generated API profiles explicitly set:

```toml
cli_auth_credentials_store = "file"
```

This prevents a desktop or Keychain ChatGPT login from overriding the selected API key.

After switching, restart existing Codex CLI or desktop processes so they reload the credential store and config.

## Existing duplicates

v0.8.1 prevents new duplicate imports but does not automatically delete existing profiles because two profiles for the same account may intentionally use different models or endpoints. Inspect them first, then remove only the unwanted profile.
