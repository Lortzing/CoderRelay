from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from . import __version__
from .errors import InvalidProfileError

CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
USER_AGENT = f"coder-relay/{__version__}"


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        padding = "=" * (-len(part) % 4)
        decoded = base64.urlsafe_b64decode(part + padding)
        value = json.loads(decoded)
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_auth_payload(raw: Any, *, source: str = "auth.json") -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise InvalidProfileError(f"{source} must contain a JSON object.")

    api_key = raw.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        return {
            "kind": "api",
            "auth_mode": raw.get("auth_mode") or "apikey",
            "api_key": api_key.strip(),
        }

    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        raise InvalidProfileError(
            f"{source}: ChatGPT auth must contain tokens; API auth must contain OPENAI_API_KEY."
        )
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    id_token = tokens.get("id_token")
    if not isinstance(access_token, str) or not access_token:
        raise InvalidProfileError(f"{source} is missing tokens.access_token.")
    claims = _decode_jwt_payload(id_token) if isinstance(id_token, str) else {}
    openai_claims = claims.get("https://api.openai.com/auth")
    if not isinstance(openai_claims, dict):
        openai_claims = {}
    if not isinstance(account_id, str) or not account_id:
        account_id = openai_claims.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id:
        raise InvalidProfileError(f"{source} is missing a ChatGPT account id.")

    return {
        "kind": "chatgpt",
        "auth_mode": raw.get("auth_mode") or "chatgpt",
        "access_token": access_token,
        "account_id": account_id,
        "email": claims.get("email"),
        "plan": openai_claims.get("chatgpt_plan_type"),
        "user_id": openai_claims.get("chatgpt_user_id") or openai_claims.get("user_id"),
        "last_refresh": raw.get("last_refresh"),
    }


def parse_auth_bytes(content: bytes, *, source: str = "auth data") -> dict[str, Any]:
    try:
        raw = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidProfileError(f"Invalid auth JSON from {source}: {exc}") from exc
    return parse_auth_payload(raw, source=source)


def parse_auth_json(path: Path) -> dict[str, Any]:
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise InvalidProfileError(f"Missing auth file: {path}") from exc
    return parse_auth_bytes(content, source=str(path))


def _reset_iso(timestamp: Any) -> str | None:
    if not isinstance(timestamp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(timestamp, UTC).replace(microsecond=0).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def parse_usage_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        rate_limit = {}

    def window(name: str) -> dict[str, Any] | None:
        value = rate_limit.get(name)
        if not isinstance(value, dict):
            return None
        used = value.get("used_percent")
        seconds = value.get("limit_window_seconds")
        return {
            "used_percent": float(used) if isinstance(used, (int, float)) else None,
            "window_minutes": (int(seconds) + 59) // 60 if isinstance(seconds, int) else None,
            "reset_at": _reset_iso(value.get("reset_at")),
        }

    credits = payload.get("credits") if isinstance(payload.get("credits"), dict) else {}
    reset_credits = (
        payload.get("rate_limit_reset_credits")
        if isinstance(payload.get("rate_limit_reset_credits"), dict)
        else {}
    )
    return {
        "plan_type": payload.get("plan_type"),
        "primary": window("primary_window"),
        "secondary": window("secondary_window"),
        "credits": {
            "has_credits": credits.get("has_credits"),
            "unlimited": credits.get("unlimited"),
            "balance": credits.get("balance"),
        },
        "reset_credits_available": reset_credits.get("available_count"),
    }


def fetch_chatgpt_usage(
    auth_path: Path,
    *,
    timeout: float = 15.0,
    client: httpx.Client | None = None,
) -> tuple[dict[str, Any], int]:
    auth = parse_auth_json(auth_path)
    if auth["kind"] != "chatgpt":
        raise InvalidProfileError("ChatGPT usage is unavailable for API-key profiles.")

    own_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        response = client.get(
            CHATGPT_USAGE_URL,
            headers={
                "Authorization": f"Bearer {auth['access_token']}",
                "ChatGPT-Account-Id": auth["account_id"],
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
    finally:
        if own_client:
            client.close()
    if response.status_code < 200 or response.status_code >= 300:
        detail = response.text[:300].strip()
        raise httpx.HTTPStatusError(
            f"ChatGPT usage endpoint returned HTTP {response.status_code}: {detail}",
            request=response.request,
            response=response,
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise InvalidProfileError("ChatGPT usage endpoint returned a non-object JSON response.")
    return parse_usage_payload(payload), response.status_code
