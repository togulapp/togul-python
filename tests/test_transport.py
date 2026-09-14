from __future__ import annotations

import pytest

from togul import _transport as t
from togul.config import Config
from togul.errors import TogulError


def config(**overrides) -> Config:
    base = {"api_key": "key", "environment": "production"}
    base.update(overrides)
    return Config(**base)


def test_cache_key_without_context():
    assert t.cache_key_for("theme", "production", None) == "theme:production"
    assert t.cache_key_for("theme", "production", {}) == "theme:production"


def test_cache_key_sorts_context_keys():
    forward = t.cache_key_for("theme", "production", {"a": "1", "b": "2"})
    reverse = t.cache_key_for("theme", "production", {"b": "2", "a": "1"})
    assert forward == reverse == "theme:production:a=1:b=2"


def test_build_request_matches_openapi_shape():
    assert t.build_request(config(), "theme", {"user_id": "u-1"}) == {
        "flag_key": "theme",
        "environment_key": "production",
        "context": {"user_id": "u-1"},
    }


def test_build_request_sends_empty_object_when_context_is_none():
    assert t.build_request(config(), "theme", None)["context"] == {}


@pytest.mark.parametrize(
    ("status", "expected"),
    [(400, False), (401, False), (403, False), (404, False), (429, True), (500, True), (503, True)],
)
def test_should_retry_only_on_429_and_5xx(status, expected):
    assert t.should_retry(status) is expected


def test_backoff_is_100ms_per_attempt_and_zero_on_first():
    assert t.backoff_seconds(0) == 0.0
    assert t.backoff_seconds(1) == pytest.approx(0.1)
    assert t.backoff_seconds(2) == pytest.approx(0.2)


def test_parse_result_reads_every_field():
    result = t.parse_result(
        {
            "flag_key": "user_config",
            "enabled": True,
            "value_type": "json",
            "value": {"theme": "dark"},
            "reason": "rule_match",
        },
        "user_config",
    )
    assert result.flag_key == "user_config"
    assert result.enabled is True
    assert result.value_type == "json"
    assert result.value == {"theme": "dark"}
    assert result.reason == "rule_match"


def test_parse_result_falls_back_when_fields_are_missing():
    result = t.parse_result({}, "theme")
    assert result.flag_key == "theme"
    assert result.enabled is False
    assert result.value_type == ""
    assert result.value is None
    assert result.reason == ""


def test_parse_result_tolerates_a_non_dict_payload():
    result = t.parse_result("nonsense", "theme")
    assert result.flag_key == "theme"
    assert result.value_type == ""


@pytest.mark.parametrize(
    ("value_type", "value"),
    [
        ("number", 0),
        ("boolean", False),
        ("string", ""),
        ("json", []),
    ],
)
def test_parse_result_preserves_falsy_values(value_type, value):
    """Regression guard for FIX 3.

    `parse_result` is internally inconsistent on purpose right now: `value`
    uses a bare `.get()` while `flag_key`/`value_type`/`reason` use an
    `or`-collapse and `enabled` uses `.get("enabled", False)`. That mix is
    *correct* today — a falsy `value` of `0`, `False`, `""` or `[]` survives
    intact — but nothing pinned it, which invites a future "make these
    uniform" cleanup that would silently turn every falsy flag value into
    `None` in the parity core both clients call. Do not "fix" this
    inconsistency; this test exists to keep it exactly as it is.
    """
    result = t.parse_result({"value": value, "value_type": value_type}, "f")
    assert result.value == value
    if value is False:
        assert result.value is False


def test_parse_result_preserves_an_explicit_false_enabled():
    result = t.parse_result({"enabled": False, "value_type": "boolean"}, "f")
    assert result.enabled is False


def test_api_error_reads_code_and_message():
    error = t.api_error_from(403, {"code": "forbidden", "message": "no access"})
    assert error.status_code == 403
    assert error.error_code == "forbidden"
    assert error.message == "no access"


def test_api_error_falls_back_when_body_is_unusable():
    error = t.api_error_from(500, None)
    assert error.status_code == 500
    assert error.error_code is None
    assert error.message == "Unexpected status 500"


def test_require_api_key_rejects_blank_keys():
    for blank in ("", "   "):
        with pytest.raises(TogulError, match="api_key is required"):
            t.require_api_key(config(api_key=blank))


def test_require_api_key_accepts_a_real_key():
    t.require_api_key(config())
