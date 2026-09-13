from __future__ import annotations

import dataclasses
from typing import Any, Literal

ValueType = Literal["boolean", "string", "number", "json"]


@dataclasses.dataclass(frozen=True)
class EvaluateResult:
    """A single flag evaluation, mirroring the API's ``EvaluateResponse``.

    Attributes:
        flag_key: The evaluated flag key.
        enabled: Whether the flag itself is enabled.
        value_type: One of ``boolean``, ``string``, ``number``, ``json``.
            An empty string means the API response was malformed.
        value: The flag value. Its Python type follows ``value_type``.
        reason: One of ``disabled``, ``rule_match``, ``default``.
    """

    flag_key: str
    enabled: bool
    value_type: str
    value: Any
    reason: str
