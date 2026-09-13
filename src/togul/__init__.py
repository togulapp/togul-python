"""Official Python SDK for Togul feature flags and remote config."""

from __future__ import annotations

from .async_client import AsyncTogulClient
from .client import TogulClient
from .config import DEFAULT_BASE_URL, Config
from .errors import TogulAPIError, TogulError
from .result import EvaluateResult, ValueType
from .stream import AsyncTogulStreamClient, TogulStreamClient

__version__ = "0.1.0"

__all__ = [
    "AsyncTogulClient",
    "AsyncTogulStreamClient",
    "Config",
    "DEFAULT_BASE_URL",
    "EvaluateResult",
    "TogulAPIError",
    "TogulClient",
    "TogulError",
    "TogulStreamClient",
    "ValueType",
]
