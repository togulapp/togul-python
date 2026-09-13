from __future__ import annotations

import pytest

from togul._sse import parse_sse_event


def test_parses_a_flag_updated_event():
    line = 'data: {"type":"flag.updated","flag_key":"new-dashboard","version":0}'
    assert parse_sse_event(line) == {
        "type": "flag.updated",
        "flag_key": "new-dashboard",
        "version": 0,
    }


def test_parses_an_event_without_a_flag_key():
    assert parse_sse_event('data: {"type":"rule.deleted"}') == {"type": "rule.deleted"}


@pytest.mark.parametrize(
    "line",
    [
        ": heartbeat",
        "",
        "   ",
        "event: flag.updated",
        "id: 42",
        "retry: 1000",
        "data:",
        "data: ",
        "data: not json",
        "data: [1, 2, 3]",
        'data: "a string"',
        "data: 123",
        "data: null",
    ],
)
def test_ignores_everything_that_is_not_a_json_object_data_line(line):
    assert parse_sse_event(line) is None


def test_tolerates_trailing_whitespace_and_carriage_returns():
    assert parse_sse_event('data: {"flag_key":"f"}  \r') == {"flag_key": "f"}
