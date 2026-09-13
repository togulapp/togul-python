from __future__ import annotations

import dataclasses

DEFAULT_BASE_URL = "https://api.togul.io"


@dataclasses.dataclass(frozen=True)
class Config:
    """Connection and caching settings for a Togul client.

    Args:
        api_key: Environment-scoped API key (``server`` or ``sdk`` scope to
            evaluate, ``sdk`` or ``stream`` scope to stream).
        environment: Environment key, e.g. ``"production"``.
        timeout: HTTP request timeout in seconds.
        cache_ttl: How long an evaluation result stays cached, in seconds.
        retry_count: Total request attempts, not extra retries. ``2`` means
            the SDK issues at most two requests.
        base_url: API origin. Trailing slashes are stripped.
    """

    api_key: str
    environment: str
    timeout: float = 5.0
    cache_ttl: float = 30.0
    retry_count: int = 2
    base_url: str = DEFAULT_BASE_URL

    def __post_init__(self) -> None:
        normalized = self.base_url.rstrip("/") or DEFAULT_BASE_URL
        object.__setattr__(self, "base_url", normalized)
