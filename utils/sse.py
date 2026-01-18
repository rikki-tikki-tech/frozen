import json
from typing import Any


def sse_event(data: dict[str, Any]) -> str:
    """Format data as Server-Sent Event."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
