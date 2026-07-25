import json
from pathlib import Path

import httpx

from coder_relay.health import probe_profile
from coder_relay.models import HealthConfig, Profile


def _profile() -> Profile:
    return Profile(
        name="gateway",
        kind="api",
        created_at="now",
        updated_at="now",
        model="gpt-test",
        base_url="https://gateway.example/v1",
        provider_id="gateway",
        health=HealthConfig(mode="responses"),
    )


def test_html_200_is_not_a_healthy_responses_api(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "secret"}))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><script>challenge()</script></html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe_profile(_profile(), auth, client=client)

    assert result.healthy is False
    assert result.status == "invalid_response"
    assert result.message == "Expected JSON response; got text/html."


def test_json_without_output_is_not_healthy(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "secret"}))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"}, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe_profile(_profile(), auth, client=client)

    assert result.healthy is False
    assert result.status == "invalid_response"
