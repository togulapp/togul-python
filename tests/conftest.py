from __future__ import annotations

from typing import Any, List, Optional

import httpx
import pytest

from togul.config import Config


@pytest.fixture
def config() -> Config:
    return Config(
        api_key="test-key",
        environment="production",
        base_url="https://api.test",
        cache_ttl=30.0,
        retry_count=2,
    )


class Recorder:
    """Collects the requests a mock transport received."""

    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []

    @property
    def count(self) -> int:
        return len(self.requests)


def make_transport(
    responses: List[httpx.Response],
    recorder: Optional[Recorder] = None,
) -> httpx.MockTransport:
    """Return a transport that replays `responses` in order.

    The final response repeats if more requests arrive than responses given.
    """
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.requests.append(request)
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return httpx.MockTransport(handler)


def ok(
    value: Any = True,
    value_type: str = "boolean",
    flag_key: str = "new-dashboard",
    reason: str = "rule_match",
    enabled: bool = True,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "flag_key": flag_key,
            "enabled": enabled,
            "value_type": value_type,
            "value": value,
            "reason": reason,
        },
    )


def err(status: int, code: str = "bad", message: str = "bad request") -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": message})


@pytest.fixture
def no_sleep(monkeypatch) -> List[float]:
    """Replace time.sleep in the sync client and record the delays."""
    import togul.client

    slept: List[float] = []
    monkeypatch.setattr(togul.client.time, "sleep", lambda seconds: slept.append(seconds))
    return slept
