from __future__ import annotations

import togul.cache as cache_module
from togul.cache import TTLCache
from togul.result import EvaluateResult


def make_result(flag_key: str = "flag", value_type: str = "boolean") -> EvaluateResult:
    return EvaluateResult(
        flag_key=flag_key,
        enabled=True,
        value_type=value_type,
        value=True,
        reason="rule_match",
    )


def test_get_returns_none_for_unknown_key():
    assert TTLCache(30.0).get("missing") is None


def test_set_then_get_round_trips():
    cache = TTLCache(30.0)
    result = make_result()
    cache.set("flag:production", result)
    assert cache.get("flag:production") == result


def test_expired_entry_is_evicted(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: clock["now"])

    cache = TTLCache(30.0)
    cache.set("flag:production", make_result())

    clock["now"] = 1029.0
    assert cache.get("flag:production") is not None

    clock["now"] = 1031.0
    assert cache.get("flag:production") is None


def test_entry_with_blank_value_type_is_treated_as_stale():
    cache = TTLCache(30.0)
    cache.set("flag:production", make_result(value_type=""))
    assert cache.get("flag:production") is None
    # The stale entry is dropped, not merely hidden.
    assert cache.get("flag:production") is None


def test_flush_clears_everything():
    cache = TTLCache(30.0)
    cache.set("a:production", make_result("a"))
    cache.set("b:production", make_result("b"))
    cache.flush()
    assert cache.get("a:production") is None
    assert cache.get("b:production") is None


def test_invalidate_flag_removes_only_that_flags_entries():
    cache = TTLCache(30.0)
    cache.set("theme:production", make_result("theme"))
    cache.set("theme:production:user_id=1", make_result("theme"))
    cache.set("other:production", make_result("other"))

    cache.invalidate_flag("theme")

    assert cache.get("theme:production") is None
    assert cache.get("theme:production:user_id=1") is None
    assert cache.get("other:production") is not None


def test_evaluate_result_is_frozen_and_comparable():
    assert make_result() == make_result()
