from __future__ import annotations

import dataclasses

import pytest

from togul.config import DEFAULT_BASE_URL, Config
from togul.errors import TogulAPIError, TogulError


def test_defaults_match_other_sdks():
    config = Config(api_key="k", environment="production")
    assert config.timeout == 5.0
    assert config.cache_ttl == 30.0
    assert config.retry_count == 2
    assert config.base_url == DEFAULT_BASE_URL


def test_base_url_trailing_slashes_are_stripped():
    assert (
        Config(api_key="k", environment="e", base_url="https://x.dev///").base_url
        == "https://x.dev"
    )


def test_blank_base_url_falls_back_to_default():
    assert Config(api_key="k", environment="e", base_url="").base_url == DEFAULT_BASE_URL


def test_config_is_frozen():
    config = Config(api_key="k", environment="e")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.api_key = "other"  # type: ignore[misc]


def test_api_error_is_a_togul_error_and_carries_metadata():
    error = TogulAPIError("nope", status_code=403, error_code="forbidden")
    assert isinstance(error, TogulError)
    assert error.status_code == 403
    assert error.error_code == "forbidden"
    assert error.message == "nope"
    assert str(error) == "nope"


def test_api_error_defaults():
    error = TogulAPIError("boom")
    assert error.status_code == 0
    assert error.error_code is None
