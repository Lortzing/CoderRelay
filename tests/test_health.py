import json
from pathlib import Path

import httpx

from coder_relay.health import _display_message, probe_profile
from coder_relay.models import HealthConfig, Profile


def test_display_message_keeps_short_details_and_hides_long_ones():
    assert _display_message(" short\nmessage ") == "short message"
    assert _display_message("x" * 60) == "x" * 60
    assert _display_message("x" * 61) is None
    assert _display_message("") is None


def test_api_responses_probe(tmp_path: Path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "secret"}))
    profile = Profile(
        name="api",
        kind="api",
        created_at="now",
        updated_at="now",
        model="gpt-test",
        base_url="https://example.test/v1",
        provider_id="example",
        health=HealthConfig(mode="responses", expected_text="OK"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://example.test/v1/responses"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"output_text": "OK"}, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe_profile(profile, auth, client=client)
    assert result.healthy is True
    assert result.status == "healthy"


def test_api_auth_error(tmp_path: Path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "bad"}))
    profile = Profile(
        name="api",
        kind="api",
        created_at="now",
        updated_at="now",
        model="gpt-test",
        base_url="https://example.test/v1",
        health=HealthConfig(mode="responses"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid"}, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe_profile(profile, auth, client=client)
    assert result.healthy is False
    assert result.status == "auth_error"


def test_api_balance_and_redaction(tmp_path: Path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "secret"}))
    from coder_relay.models import BalanceConfig

    profile = Profile(
        name="api",
        kind="api",
        created_at="now",
        updated_at="now",
        model="gpt-test",
        base_url="https://example.test/v1",
        health=HealthConfig(mode="responses"),
        balance=BalanceConfig("https://example.test/balance", "data.balance"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/responses":
            return httpx.Response(200, json={"output_text": "OK"}, request=request)
        return httpx.Response(200, json={"data": {"balance": 42}}, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe_profile(profile, auth, client=client)
    assert result.healthy is True
    assert result.balance == 42
