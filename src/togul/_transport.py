from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .config import Config
from .errors import TogulAPIError, TogulError
from .result import EvaluateResult

EVALUATE_PATH = "/api/v1/evaluate"
STREAM_PATH = "/api/v1/stream"


def cache_key_for(
    flag_key: str,
    environment: str,
    context: Optional[Mapping[str, str]],
) -> str:
    """Build the cache key for one evaluation.

    Format: ``flag_key:environment`` followed by ``:key=value`` for every
    context pair, with keys sorted so call order never changes the key.
    Identical to ``cacheKeyFor`` (Go), ``cacheKey`` (JS) and
    ``buildCacheKey`` (PHP).
    """
    parts = [flag_key, environment]
    if context:
        for key in sorted(context):
            parts.append(f"{key}={context[key]}")
    return ":".join(parts)


def build_request(
    config: Config,
    flag_key: str,
    context: Optional[Mapping[str, str]],
) -> Dict[str, Any]:
    return {
        "flag_key": flag_key,
        "environment_key": config.environment,
        "context": dict(context) if context else {},
    }


def should_retry(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def backoff_seconds(attempt: int) -> float:
    return attempt * 0.1


def parse_result(payload: Any, flag_key: str) -> EvaluateResult:
    if not isinstance(payload, dict):
        payload = {}
    return EvaluateResult(
        flag_key=str(payload.get("flag_key") or flag_key),
        enabled=bool(payload.get("enabled", False)),
        value_type=str(payload.get("value_type") or ""),
        value=payload.get("value"),
        reason=str(payload.get("reason") or ""),
    )


def api_error_from(status_code: int, body: Any) -> TogulAPIError:
    message = f"Unexpected status {status_code}"
    error_code: Optional[str] = None
    if isinstance(body, dict):
        if body.get("message"):
            message = str(body["message"])
        if body.get("code"):
            error_code = str(body["code"])
    return TogulAPIError(message, status_code=status_code, error_code=error_code)


def require_api_key(config: Config) -> None:
    if not config.api_key.strip():
        raise TogulError("togul: api_key is required")
