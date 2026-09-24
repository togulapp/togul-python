"""OpenFeature provider for Togul.

Requires the optional ``openfeature`` extra (``pip install togul[openfeature]``).

Caching, retries and SSE invalidation all stay in the Togul client; this module
only adapts the single ``evaluate()`` call to OpenFeature's typed resolvers and
never raises. Cache invalidations are re-emitted as
``PROVIDER_CONFIGURATION_CHANGED``. Mirrors the PHP provider
(``togul-php/src/OpenFeature/TogulProvider.php``).
"""

from __future__ import annotations

import datetime
import json
import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, TypeVar, Union

from openfeature.evaluation_context import EvaluationContext
from openfeature.event import ProviderEventDetails
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagResolutionDetails, FlagValueType, Reason
from openfeature.provider import AbstractProvider, Metadata

from ..async_client import AsyncTogulClient
from ..client import TogulClient
from ..errors import TogulAPIError
from ..result import EvaluateResult

T = TypeVar("T")

Coerce = Callable[[Any], Tuple[bool, Any]]
ObjectValue = Union[Sequence[FlagValueType], Mapping[str, FlagValueType]]

__all__ = ["TogulProvider"]


class TogulProvider(AbstractProvider):
    """OpenFeature provider backed by a Togul client.

    Pass a ``TogulClient`` for the synchronous OpenFeature API, an
    ``AsyncTogulClient`` (``async_client=``) for the ``*_async`` API, or both.
    Without an async client the async API falls back to the sync client, which
    blocks the event loop; without a sync client the sync API resolves to the
    caller's default with ``GENERAL``.
    """

    def __init__(
        self,
        client: Optional[TogulClient] = None,
        *,
        async_client: Optional[AsyncTogulClient] = None,
        targeting_key_attribute: str = "user_id",
    ) -> None:
        if client is None and async_client is None:
            raise ValueError("TogulProvider needs a TogulClient, an AsyncTogulClient, or both")
        super().__init__()
        self._client = client
        self._async_client = async_client
        # Context attribute the targeting key is sent as. "user_id" matches the
        # default rule "bucket_by".
        self._targeting_key_attribute = targeting_key_attribute
        self._closed = False
        for togul in (client, async_client):
            if togul is not None:
                togul.on_cache_invalidated(self._on_invalidated)

    @property
    def client(self) -> Optional[TogulClient]:
        return self._client

    @property
    def async_client(self) -> Optional[AsyncTogulClient]:
        return self._async_client

    def get_metadata(self) -> Metadata:
        return Metadata(name="Togul")

    def get_provider_hooks(self) -> List[Any]:
        return []

    def shutdown(self) -> None:
        """Stop forwarding invalidations. The clients are left to their owner."""
        self._closed = True

    def _on_invalidated(self, flag_key: str) -> None:
        if self._closed:
            return
        self.emit_provider_configuration_changed(
            ProviderEventDetails(
                flags_changed=[flag_key] if flag_key else None,
                message="Togul cache invalidated",
            )
        )

    # -- sync resolvers -------------------------------------------------------

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[bool]:
        return self._resolve(flag_key, default_value, evaluation_context, "boolean", _coerce_bool)

    def resolve_string_details(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[str]:
        return self._resolve(flag_key, default_value, evaluation_context, "string", _coerce_str)

    def resolve_integer_details(
        self,
        flag_key: str,
        default_value: int,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[int]:
        return self._resolve(flag_key, default_value, evaluation_context, "integer", _coerce_int)

    def resolve_float_details(
        self,
        flag_key: str,
        default_value: float,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[float]:
        return self._resolve(flag_key, default_value, evaluation_context, "float", _coerce_float)

    def resolve_object_details(
        self,
        flag_key: str,
        default_value: ObjectValue,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[ObjectValue]:
        return self._resolve(flag_key, default_value, evaluation_context, "object", _coerce_object)

    # -- async resolvers ------------------------------------------------------

    async def resolve_boolean_details_async(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[bool]:
        return await self._resolve_async(
            flag_key, default_value, evaluation_context, "boolean", _coerce_bool
        )

    async def resolve_string_details_async(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[str]:
        return await self._resolve_async(
            flag_key, default_value, evaluation_context, "string", _coerce_str
        )

    async def resolve_integer_details_async(
        self,
        flag_key: str,
        default_value: int,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[int]:
        return await self._resolve_async(
            flag_key, default_value, evaluation_context, "integer", _coerce_int
        )

    async def resolve_float_details_async(
        self,
        flag_key: str,
        default_value: float,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[float]:
        return await self._resolve_async(
            flag_key, default_value, evaluation_context, "float", _coerce_float
        )

    async def resolve_object_details_async(
        self,
        flag_key: str,
        default_value: ObjectValue,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[ObjectValue]:
        return await self._resolve_async(
            flag_key, default_value, evaluation_context, "object", _coerce_object
        )

    # -- shared ---------------------------------------------------------------

    def _resolve(
        self,
        flag_key: str,
        default_value: T,
        evaluation_context: Optional[EvaluationContext],
        expected_type: str,
        coerce: Coerce,
    ) -> FlagResolutionDetails[T]:
        if self._client is None:
            return _error(
                default_value,
                ErrorCode.GENERAL,
                "TogulProvider has no sync client; use the async API or pass a TogulClient",
            )
        try:
            result = self._client.evaluate(flag_key, self._togul_context(evaluation_context))
        except Exception as exc:  # noqa: BLE001 - a provider must never raise
            return _evaluation_error(default_value, exc)
        return _details(flag_key, default_value, result, expected_type, coerce)

    async def _resolve_async(
        self,
        flag_key: str,
        default_value: T,
        evaluation_context: Optional[EvaluationContext],
        expected_type: str,
        coerce: Coerce,
    ) -> FlagResolutionDetails[T]:
        if self._async_client is None:
            return self._resolve(flag_key, default_value, evaluation_context, expected_type, coerce)
        try:
            result = await self._async_client.evaluate(
                flag_key, self._togul_context(evaluation_context)
            )
        except Exception as exc:  # noqa: BLE001 - a provider must never raise
            return _evaluation_error(default_value, exc)
        return _details(flag_key, default_value, result, expected_type, coerce)

    def _togul_context(self, evaluation_context: Optional[EvaluationContext]) -> Dict[str, str]:
        """Flatten an OpenFeature context into Togul's string-only context."""
        if evaluation_context is None:
            return {}

        out: Dict[str, str] = {}
        for key, value in evaluation_context.attributes.items():
            string_value = _stringify_attribute(value)
            if string_value is not None:
                out[key] = string_value

        # An explicitly set attribute wins over the targeting key.
        targeting_key = evaluation_context.targeting_key
        if targeting_key and self._targeting_key_attribute not in out:
            out[self._targeting_key_attribute] = targeting_key
        return out


def _details(
    flag_key: str,
    default_value: T,
    result: EvaluateResult,
    expected_type: str,
    coerce: Coerce,
) -> FlagResolutionDetails[T]:
    # OpenFeature spec: a disabled flag resolves to the caller's default.
    if not result.enabled:
        return FlagResolutionDetails(value=default_value, reason=Reason.DISABLED)

    # A json flag created without a default stores null: nothing to serve.
    if result.value is None:
        return FlagResolutionDetails(value=default_value, reason=_map_reason(result.reason))

    matches, value = coerce(result.value)
    if not matches:
        return _error(
            default_value,
            ErrorCode.TYPE_MISMATCH,
            f'Flag "{flag_key}" has value_type "{result.value_type}", requested {expected_type}',
        )
    return FlagResolutionDetails(value=value, reason=_map_reason(result.reason))


def _map_reason(reason: str) -> Reason:
    return {
        "rule_match": Reason.TARGETING_MATCH,
        "default": Reason.DEFAULT,
        "disabled": Reason.DISABLED,
    }.get(reason, Reason.UNKNOWN)


def _evaluation_error(default_value: T, exc: Exception) -> FlagResolutionDetails[T]:
    not_found = (
        isinstance(exc, TogulAPIError)
        and exc.status_code == 404
        and exc.error_code == "evaluate.flag_not_found"
    )
    code = ErrorCode.FLAG_NOT_FOUND if not_found else ErrorCode.GENERAL
    return _error(default_value, code, str(exc))


def _error(default_value: T, code: ErrorCode, message: str) -> FlagResolutionDetails[T]:
    return FlagResolutionDetails(
        value=default_value, reason=Reason.ERROR, error_code=code, error_message=message
    )


# bool is a subclass of int in Python: every numeric coercion rejects it.


def _coerce_bool(value: Any) -> Tuple[bool, Any]:
    return isinstance(value, bool), value


def _coerce_str(value: Any) -> Tuple[bool, Any]:
    return isinstance(value, str), value


def _coerce_int(value: Any) -> Tuple[bool, Any]:
    # Togul has a single "number" type; JSON decoding yields float for "3.0".
    if isinstance(value, bool):
        return False, None
    if isinstance(value, int):
        return True, value
    if isinstance(value, float) and value.is_integer():
        return True, int(value)
    return False, None


def _coerce_float(value: Any) -> Tuple[bool, Any]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, None
    return True, float(value)


def _coerce_object(value: Any) -> Tuple[bool, Any]:
    return isinstance(value, (dict, list)), value


def _stringify_attribute(value: Any) -> Optional[str]:
    """Convert one attribute to the string Togul rules compare against.

    Rules compare with plain string equality ("eq", "in", ...), so every format
    must match what users type in the dashboard. ``None`` drops the attribute.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
    return str(value)
