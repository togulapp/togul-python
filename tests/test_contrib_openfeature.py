from __future__ import annotations

import datetime
import json
from typing import Any, List

import httpx
import pytest
from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.event import ProviderEvent
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import Reason

from togul.async_client import AsyncTogulClient
from togul.client import TogulClient
from togul.contrib.openfeature import TogulProvider

from .conftest import Recorder, err, make_transport, ok


def sync_client(config, responses: List[httpx.Response], recorder: Recorder = None) -> TogulClient:
    return TogulClient(
        config,
        http_client=httpx.Client(
            transport=make_transport(responses, recorder), base_url=config.base_url
        ),
    )


def async_client(
    config, responses: List[httpx.Response], recorder: Recorder = None
) -> AsyncTogulClient:
    return AsyncTogulClient(
        config,
        http_client=httpx.AsyncClient(
            transport=make_transport(responses, recorder), base_url=config.base_url
        ),
    )


def sent_context(recorder: Recorder) -> Any:
    return json.loads(recorder.requests[-1].content)["context"]


def test_requires_a_client():
    with pytest.raises(ValueError):
        TogulProvider()


def test_resolves_each_type_with_mapped_reasons(config):
    provider = TogulProvider(sync_client(config, [ok()]))
    details = provider.resolve_boolean_details("new-dashboard", False)
    assert (details.value, details.reason) == (True, Reason.TARGETING_MATCH)

    provider = TogulProvider(sync_client(config, [ok("dark", "string", reason="default")]))
    details = provider.resolve_string_details("theme", "light")
    assert (details.value, details.reason) == ("dark", Reason.DEFAULT)

    provider = TogulProvider(sync_client(config, [ok(42, "number")]))
    assert provider.resolve_integer_details("n", 0).value == 42
    assert provider.resolve_float_details("n2", 0.0).value == 42.0

    provider = TogulProvider(sync_client(config, [ok({"theme": "dark"}, "json")]))
    assert provider.resolve_object_details("o", {}).value == {"theme": "dark"}


def test_unknown_reason_maps_to_unknown(config):
    provider = TogulProvider(sync_client(config, [ok(reason="something_new")]))
    assert provider.resolve_boolean_details("f", False).reason == Reason.UNKNOWN


def test_whole_float_is_an_int_but_fraction_is_a_mismatch(config):
    provider = TogulProvider(sync_client(config, [ok(3.0, "number")]))
    assert provider.resolve_integer_details("whole", 0).value == 3

    provider = TogulProvider(sync_client(config, [ok(1.5, "number")]))
    details = provider.resolve_integer_details("frac", 7)
    assert (details.value, details.error_code) == (7, ErrorCode.TYPE_MISMATCH)


def test_bool_is_not_a_number(config):
    provider = TogulProvider(sync_client(config, [ok(True)]))
    assert provider.resolve_integer_details("f", 5).error_code == ErrorCode.TYPE_MISMATCH


def test_disabled_and_null_serve_the_default(config):
    provider = TogulProvider(sync_client(config, [ok(True, enabled=False, reason="disabled")]))
    details = provider.resolve_boolean_details("f", False)
    assert (details.value, details.reason) == (False, Reason.DISABLED)

    provider = TogulProvider(sync_client(config, [ok(None, "json", reason="default")]))
    details = provider.resolve_object_details("o", {"a": 1})
    assert (details.value, details.reason) == ({"a": 1}, Reason.DEFAULT)


@pytest.mark.parametrize(
    "response, code",
    [
        (ok("dark", "string"), ErrorCode.TYPE_MISMATCH),
        (err(404, "evaluate.flag_not_found", "Flag not found"), ErrorCode.FLAG_NOT_FOUND),
        (err(401, "unauthorized", "nope"), ErrorCode.GENERAL),
    ],
)
def test_errors_resolve_to_the_default(config, response, code):
    provider = TogulProvider(sync_client(config, [response]))
    details = provider.resolve_boolean_details("f", True)
    assert (details.value, details.reason, details.error_code) == (True, Reason.ERROR, code)


def test_flattens_the_context(config):
    recorder = Recorder()
    provider = TogulProvider(sync_client(config, [ok()], recorder))
    provider.resolve_boolean_details(
        "f",
        False,
        EvaluationContext(
            "u1",
            {
                "country": "TR",
                "beta": True,
                "age": 42,
                "score": 1.5,
                "whole": 2.0,
                "signup": datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc),
                "tags": ["a", "b"],
                "missing": None,
            },
        ),
    )
    assert sent_context(recorder) == {
        "user_id": "u1",
        "country": "TR",
        "beta": "true",
        "age": "42",
        "score": "1.5",
        "whole": "2",
        "signup": "2026-01-02T03:04:05+00:00",
        "tags": '["a","b"]',
    }


def test_targeting_key_attribute(config):
    recorder = Recorder()
    provider = TogulProvider(sync_client(config, [ok()], recorder))
    provider.resolve_boolean_details("f", False, EvaluationContext("tk", {"user_id": "explicit"}))
    assert sent_context(recorder)["user_id"] == "explicit"

    recorder = Recorder()
    provider = TogulProvider(
        sync_client(config, [ok()], recorder), targeting_key_attribute="account_id"
    )
    provider.resolve_boolean_details("f", False, EvaluationContext("acc-1"))
    assert sent_context(recorder) == {"account_id": "acc-1"}


async def test_async_resolvers_use_the_async_client(config):
    recorder = Recorder()
    provider = TogulProvider(async_client=async_client(config, [ok("dark", "string")], recorder))
    details = await provider.resolve_string_details_async("theme", "light", EvaluationContext("u1"))
    assert details.value == "dark"
    assert sent_context(recorder) == {"user_id": "u1"}

    details = provider.resolve_string_details("theme", "light")
    assert (details.value, details.error_code) == ("light", ErrorCode.GENERAL)


async def test_async_resolvers_fall_back_to_the_sync_client(config):
    provider = TogulProvider(sync_client(config, [ok(7, "number")]))
    assert (await provider.resolve_integer_details_async("n", 0)).value == 7


async def test_async_errors_resolve_to_the_default(config):
    provider = TogulProvider(
        async_client=async_client(config, [err(404, "evaluate.flag_not_found", "Flag not found")])
    )
    details = await provider.resolve_boolean_details_async("f", True)
    assert (details.value, details.error_code) == (True, ErrorCode.FLAG_NOT_FOUND)


def test_forwards_invalidations_until_shutdown(config):
    client = sync_client(config, [ok()])
    provider = TogulProvider(client)
    events = []
    provider._on_emit = lambda _provider, event, details: events.append((event, details))

    client.invalidate_flag("banner")
    client.invalidate_cache()
    assert [e for e, _ in events] == [ProviderEvent.PROVIDER_CONFIGURATION_CHANGED] * 2
    assert events[0][1].flags_changed == ["banner"]
    assert events[1][1].flags_changed is None

    provider.shutdown()
    client.invalidate_cache()
    assert len(events) == 2


def test_end_to_end_through_the_openfeature_api(config):
    recorder = Recorder()
    api.set_provider(TogulProvider(sync_client(config, [ok("dark", "string")], recorder)))
    try:
        value = api.get_client().get_string_value("theme", "light", EvaluationContext("u1"))
    finally:
        api.shutdown()
    assert value == "dark"
    assert sent_context(recorder)["user_id"] == "u1"
