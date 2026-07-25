from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from . import __version__
from .models import ProbeResult, Profile
from .usage import fetch_chatgpt_usage, parse_auth_json

USER_AGENT = f"coder-relay/{__version__}"
MAX_DETAIL_LENGTH = 60


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "<redacted>") if secret else text


def _display_message(text: str | None) -> str | None:
    """Return a compact status detail or omit verbose diagnostics entirely."""
    normalized = " ".join((text or "").split())
    if not normalized or len(normalized) > MAX_DETAIL_LENGTH:
        return None
    return normalized


def _endpoint(base_url: str, suffix: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", suffix.lstrip("/"))


def _extract_output_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    parts: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
    return "\n".join(parts)


def _json_path(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _classify_status(code: int) -> str:
    if code in {401, 403}:
        return "auth_error"
    if code == 429:
        return "rate_limited"
    if 500 <= code <= 599:
        return "provider_error"
    return "http_error"


def _invalid_json_message(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "unknown").split(";", 1)[0]
    return f"Expected JSON response; got {content_type}."


def probe_profile(
    profile: Profile,
    auth_path: Path,
    *,
    client: httpx.Client | None = None,
) -> ProbeResult:
    started = time.perf_counter()
    timeout = profile.health.timeout_seconds
    try:
        if profile.kind == "chatgpt":
            usage, http_status = fetch_chatgpt_usage(auth_path, timeout=timeout, client=client)
            return ProbeResult(
                profile=profile.name,
                healthy=True,
                status="healthy",
                latency_ms=(time.perf_counter() - started) * 1000,
                http_status=http_status,
                message=_display_message("ChatGPT authentication and usage endpoint are reachable."),
                usage=usage,
            )

        auth = parse_auth_json(auth_path)
        api_key = auth["api_key"]
        if not profile.base_url:
            raise ValueError("API profile has no base URL.")
        own_client = client is None
        http_client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
            mode = profile.health.mode
            if mode == "responses":
                endpoint = profile.health.endpoint or _endpoint(profile.base_url, "responses")
                response = http_client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "model": profile.model,
                        "input": profile.health.prompt,
                        "max_output_tokens": 16,
                        "stream": False,
                    },
                )
                try:
                    body: Any = response.json()
                except json.JSONDecodeError:
                    body = None
                text = _extract_output_text(body)
                expected = profile.health.expected_text
                if not 200 <= response.status_code < 300:
                    healthy = False
                    status = _classify_status(response.status_code)
                    message = _redact(response.text.strip()[:240], api_key)
                elif not isinstance(body, dict):
                    healthy = False
                    status = "invalid_response"
                    message = _invalid_json_message(response)
                elif not text.strip():
                    healthy = False
                    status = "invalid_response"
                    message = "Responses API returned no output text."
                elif expected is not None and expected.lower() not in text.lower():
                    healthy = False
                    status = "unexpected_response"
                    message = text.strip()[:240]
                else:
                    healthy = True
                    status = "healthy"
                    message = text.strip()[:240]
            elif mode == "models":
                endpoint = profile.health.endpoint or _endpoint(profile.base_url, "models")
                response = http_client.get(endpoint, headers=headers)
                if not 200 <= response.status_code < 300:
                    healthy = False
                    status = _classify_status(response.status_code)
                    message = _redact(response.text.strip()[:240], api_key)
                else:
                    try:
                        body = response.json()
                    except json.JSONDecodeError:
                        body = None
                    healthy = isinstance(body, (dict, list))
                    status = "healthy" if healthy else "invalid_response"
                    message = "Model listing succeeded." if healthy else _invalid_json_message(response)
            elif mode == "custom":
                if not profile.health.endpoint:
                    raise ValueError("Custom health mode requires an endpoint.")
                method = profile.health.method.upper()
                response = http_client.request(method, profile.health.endpoint, headers=headers)
                expected = profile.health.expected_text
                healthy = 200 <= response.status_code < 300 and (
                    expected is None or expected.lower() in response.text.lower()
                )
                status = "healthy" if healthy else _classify_status(response.status_code)
                message = _redact(response.text.strip()[:240], api_key)
            else:
                raise ValueError(f"Unsupported API health mode: {mode}")

            balance = None
            if healthy and profile.balance:
                balance_response = http_client.get(profile.balance.endpoint, headers=headers)
                if 200 <= balance_response.status_code < 300:
                    try:
                        balance = _json_path(balance_response.json(), profile.balance.json_path)
                    except json.JSONDecodeError:
                        balance = None

            return ProbeResult(
                profile=profile.name,
                healthy=healthy,
                status=status,
                latency_ms=(time.perf_counter() - started) * 1000,
                http_status=response.status_code,
                message=_display_message(message),
                balance=balance,
            )
        finally:
            if own_client:
                http_client.close()
    except httpx.TimeoutException as exc:
        return ProbeResult(
            profile=profile.name,
            healthy=False,
            status="timeout",
            latency_ms=(time.perf_counter() - started) * 1000,
            message=_display_message(str(exc)),
        )
    except httpx.HTTPStatusError as exc:
        return ProbeResult(
            profile=profile.name,
            healthy=False,
            status=_classify_status(exc.response.status_code),
            latency_ms=(time.perf_counter() - started) * 1000,
            http_status=exc.response.status_code,
            message=_display_message(str(exc)),
        )
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return ProbeResult(
            profile=profile.name,
            healthy=False,
            status="error",
            latency_ms=(time.perf_counter() - started) * 1000,
            message=_display_message(str(exc)),
        )
