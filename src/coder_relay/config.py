from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import tomlkit
from tomlkit.container import Container
from tomlkit.items import Table

from .errors import InvalidProfileError

DEFAULT_CONFIG = "# Managed by Codex and CoderRelay.\n"


def read_base_config(path: Path | None, fallback: Path | None = None) -> str:
    selected = path if path and path.exists() else fallback
    if selected and selected.exists():
        try:
            text = selected.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidProfileError(f"Config is not valid UTF-8: {selected}") from exc
        validate_toml(text)
        return text
    return DEFAULT_CONFIG


def validate_toml(text: str) -> None:
    try:
        tomlkit.parse(text)
    except Exception as exc:  # tomlkit exposes several parse exception types
        raise InvalidProfileError(f"Invalid TOML: {exc}") from exc


def provider_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower()
    if not slug:
        raise InvalidProfileError("Could not derive a provider id from the profile name.")
    return f"relay-{slug}"


def _document(base_text: str):
    validate_toml(base_text)
    return tomlkit.parse(base_text)


def _providers_table(doc: Container) -> Table:
    existing = doc.get("model_providers")
    if isinstance(existing, Table):
        return existing
    if existing is not None:
        del doc["model_providers"]
    table = tomlkit.table()
    doc["model_providers"] = table
    return table


def build_chatgpt_config(base_text: str, model: str | None = None) -> str:
    doc = _document(base_text)
    doc["model_provider"] = "openai"
    doc["cli_auth_credentials_store"] = "file"
    if model:
        doc["model"] = model
    return tomlkit.dumps(doc)


def build_api_config(
    base_text: str,
    *,
    profile_name: str,
    base_url: str,
    model: str,
    provider_id: str | None = None,
) -> tuple[str, str]:
    provider_id = provider_id or provider_slug(profile_name)
    normalized_url = base_url.rstrip("/")
    if not normalized_url.startswith(("http://", "https://")):
        raise InvalidProfileError("API URL must start with http:// or https://")
    if not model.strip():
        raise InvalidProfileError("API profile requires a model name.")

    doc = _document(base_text)
    doc["model"] = model.strip()
    doc["model_provider"] = provider_id
    # CoderRelay API profiles store OPENAI_API_KEY in auth.json. Force the
    # corresponding Codex credential store so a previous desktop/keyring login
    # cannot silently override the selected API profile.
    doc["cli_auth_credentials_store"] = "file"

    providers = _providers_table(doc)
    provider = tomlkit.table()
    provider.add("name", profile_name)
    provider.add("base_url", normalized_url)
    provider.add("wire_api", "responses")
    provider.add("requires_openai_auth", True)
    providers[provider_id] = provider
    return tomlkit.dumps(doc), provider_id


def inspect_codex_config(path: Path, *, auth_kind: str) -> dict[str, str | None]:
    """Extract non-secret profile metadata from an existing Codex config.toml."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise InvalidProfileError(f"Missing Codex config: {path}") from exc
    except UnicodeDecodeError as exc:
        raise InvalidProfileError(f"Config is not valid UTF-8: {path}") from exc

    validate_toml(text)
    doc = tomlkit.parse(text)

    def string_value(value) -> str | None:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return None

    model = string_value(doc.get("model"))
    provider_id = string_value(doc.get("model_provider"))
    base_url: str | None = None

    if auth_kind == "api":
        provider_id = provider_id or "openai"
        providers = doc.get("model_providers")
        if isinstance(providers, Mapping):
            provider = providers.get(provider_id)
            if isinstance(provider, Mapping):
                base_url = string_value(provider.get("base_url"))
        if base_url is None and provider_id == "openai":
            base_url = "https://api.openai.com/v1"
        if base_url is None:
            raise InvalidProfileError(
                f"Could not find model_providers.{provider_id}.base_url in {path}."
            )

    return {
        "model": model,
        "provider_id": provider_id,
        "base_url": base_url.rstrip("/") if base_url else None,
    }
