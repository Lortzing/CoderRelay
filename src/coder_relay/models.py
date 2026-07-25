from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ProfileKind = Literal["chatgpt", "api"]
HealthMode = Literal["chatgpt_usage", "responses", "models", "custom"]
AuthSource = Literal["file", "keyring"]


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class HealthConfig:
    mode: HealthMode
    timeout_seconds: float = 15.0
    endpoint: str | None = None
    method: str = "GET"
    expected_text: str | None = None
    prompt: str = "Reply with OK only."

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HealthConfig":
        return cls(
            mode=data["mode"],
            timeout_seconds=float(data.get("timeout_seconds", 15.0)),
            endpoint=data.get("endpoint"),
            method=str(data.get("method", "GET")).upper(),
            expected_text=data.get("expected_text"),
            prompt=data.get("prompt", "Reply with OK only."),
        )


@dataclass(slots=True)
class BalanceConfig:
    endpoint: str
    json_path: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BalanceConfig | None":
        if not data:
            return None
        return cls(endpoint=data["endpoint"], json_path=data.get("json_path"))


@dataclass(slots=True)
class Profile:
    name: str
    kind: ProfileKind
    created_at: str
    updated_at: str
    model: str | None = None
    base_url: str | None = None
    provider_id: str | None = None
    account_email: str | None = None
    account_plan: str | None = None
    account_id: str | None = None
    account_user_id: str | None = None
    auth_source: AuthSource = "file"
    health: HealthConfig = field(default_factory=lambda: HealthConfig(mode="responses"))
    balance: BalanceConfig | None = None
    notes: str | None = None
    schema_version: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        auth_source = data.get("auth_source", "file")
        if auth_source not in {"file", "keyring"}:
            auth_source = "file"
        return cls(
            name=data["name"],
            kind=data["kind"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            model=data.get("model"),
            base_url=data.get("base_url"),
            provider_id=data.get("provider_id"),
            account_email=data.get("account_email"),
            account_plan=data.get("account_plan"),
            account_id=data.get("account_id"),
            account_user_id=data.get("account_user_id"),
            auth_source=auth_source,
            health=HealthConfig.from_dict(data["health"]),
            balance=BalanceConfig.from_dict(data.get("balance")),
            notes=data.get("notes"),
            schema_version=int(data.get("schema_version", 1)),
        )


@dataclass(slots=True)
class ProbeResult:
    profile: str
    healthy: bool
    status: str
    latency_ms: float | None = None
    http_status: int | None = None
    message: str | None = None
    usage: dict[str, Any] | None = None
    balance: Any = None
    checked_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ActiveState:
    active_profile: str | None = None
    last_switch_at: str | None = None
    last_switch_reason: str | None = None
    counters: dict[str, dict[str, int]] = field(default_factory=dict)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ActiveState":
        data = data or {}
        return cls(
            active_profile=data.get("active_profile"),
            last_switch_at=data.get("last_switch_at"),
            last_switch_reason=data.get("last_switch_reason"),
            counters=data.get("counters", {}),
            schema_version=int(data.get("schema_version", 1)),
        )
