from __future__ import annotations

import httpx
import pytest

from togul.client import TogulClient
from togul.errors import TogulAPIError, TogulError

from .conftest import Recorder, err, make_transport, ok


def build(config, responses, recorder=None) -> TogulClient:
    transport = make_transport(responses, recorder)
    http = httpx.Client(transport=transport, base_url=config.base_url)
    return TogulClient(config, http_client=http)


def test_evaluate_returns_the_parsed_result(config):
    client = build(config, [ok()])
    result = client.evaluate("new-dashboard")
    assert result.flag_key == "new-dashboard"
    assert result.enabled is True
    assert result.value is True
    assert result.value_type == "boolean"
    assert result.reason == "rule_match"


def test_evaluate_sends_the_api_key_and_openapi_body(config):
    recorder = Recorder()
    client = build(config, [ok()], recorder)
    client.evaluate("new-dashboard", {"user_id": "u-1"})

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/evaluate"
    assert request.headers["X-API-Key"] == "test-key"
    import json

    assert json.loads(request.content) == {
        "flag_key": "new-dashboard",
        "environment_key": "production",
        "context": {"user_id": "u-1"},
    }


@pytest.mark.parametrize(
    ("value_type", "value"),
    [
        ("boolean", True),
        ("string", "dark_mode"),
        ("number", 99),
        ("json", {"theme": "dark", "notifications": True}),
    ],
)
def test_evaluate_handles_every_value_type(config, value_type, value):
    client = build(config, [ok(value=value, value_type=value_type)])
    result = client.evaluate("f")
    assert result.value_type == value_type
    assert result.value == value


def test_second_evaluate_is_served_from_cache(config):
    recorder = Recorder()
    client = build(config, [ok()], recorder)
    client.evaluate("new-dashboard")
    client.evaluate("new-dashboard")
    assert recorder.count == 1


def test_context_key_order_does_not_cause_a_second_request(config):
    recorder = Recorder()
    client = build(config, [ok()], recorder)
    client.evaluate("f", {"a": "1", "b": "2"})
    client.evaluate("f", {"b": "2", "a": "1"})
    assert recorder.count == 1


def test_different_context_causes_a_second_request(config):
    recorder = Recorder()
    client = build(config, [ok()], recorder)
    client.evaluate("f", {"user_id": "u-1"})
    client.evaluate("f", {"user_id": "u-2"})
    assert recorder.count == 2


def test_429_is_retried_then_succeeds(config, no_sleep):
    recorder = Recorder()
    client = build(config, [err(429, "rate_limited", "slow down"), ok()], recorder)
    assert client.evaluate("f").enabled is True
    assert recorder.count == 2
    assert no_sleep == [pytest.approx(0.1)]


def test_400_raises_immediately_without_retrying(config, no_sleep):
    recorder = Recorder()
    client = build(config, [err(400, "invalid", "bad flag")], recorder)
    with pytest.raises(TogulAPIError) as exc:
        client.evaluate("f")
    assert exc.value.status_code == 400
    assert exc.value.error_code == "invalid"
    assert recorder.count == 1
    assert no_sleep == []


def test_retry_exhaustion_raises(config, no_sleep):
    recorder = Recorder()
    client = build(config, [err(500, "boom", "server error")], recorder)
    with pytest.raises(TogulError):
        client.evaluate("f")
    assert recorder.count == config.retry_count == 2


def test_blank_api_key_fails_before_any_request(config):
    recorder = Recorder()
    blank = type(config)(api_key="", environment=config.environment, base_url=config.base_url)
    client = build(blank, [ok()], recorder)
    with pytest.raises(TogulError, match="api_key is required"):
        client.evaluate("f")
    assert recorder.count == 0


def test_invalidate_flag_forces_a_refetch_and_notifies_listeners(config):
    recorder = Recorder()
    client = build(config, [ok()], recorder)
    seen = []
    client.on_cache_invalidated(seen.append)

    client.evaluate("new-dashboard")
    client.invalidate_flag("new-dashboard")
    client.evaluate("new-dashboard")

    assert recorder.count == 2
    assert seen == ["new-dashboard"]


def test_invalidate_cache_clears_everything(config):
    recorder = Recorder()
    client = build(config, [ok()], recorder)
    seen = []
    client.on_cache_invalidated(seen.append)

    client.evaluate("a")
    client.evaluate("b")
    client.invalidate_cache()
    client.evaluate("a")

    assert recorder.count == 3
    assert seen == [""]


def test_works_as_a_context_manager(config):
    with build(config, [ok()]) as client:
        assert client.evaluate("f").enabled is True
