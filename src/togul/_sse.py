from __future__ import annotations

import json
from typing import Any, Dict, Optional

_DATA_PREFIX = "data: "


def parse_sse_event(line: str) -> Optional[Dict[str, Any]]:
    """Decode one SSE line into an event object.

    Returns ``None`` for anything that is not a ``data:`` line carrying a
    JSON object: heartbeat comments (``: heartbeat``), blank lines, other
    SSE fields, malformed JSON, and JSON that is not an object. The Go and
    PHP SDKs skip those cases identically.
    """
    if not line.startswith(_DATA_PREFIX):
        return None

    raw = line[len(_DATA_PREFIX) :].strip()
    if not raw:
        return None

    try:
        event = json.loads(raw)
    except ValueError:
        return None

    return event if isinstance(event, dict) else None
