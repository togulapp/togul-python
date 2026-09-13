from __future__ import annotations

from typing import Optional


class TogulError(Exception):
    """Base class for every error raised by the Togul SDK."""


class TogulAPIError(TogulError):
    """Raised when the Togul API returns a non-2xx response."""

    def __init__(
        self,
        message: str,
        status_code: int = 0,
        error_code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
