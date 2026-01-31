"""Server-Sent Events (SSE) formatting utilities."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict


class SSEMessage(BaseModel):
    """Structured SSE message payload."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    event: str
    data: BaseModel | dict[str, Any]


def sse_event(message: SSEMessage) -> str:
    """Format data as Server-Sent Event."""
    event_type = message.event.strip()
    if not event_type:
        raise ValueError("SSE event requires a non-empty 'event' name.")
    data = message.data
    if isinstance(data, BaseModel):
        json_str = data.model_dump_json()
    else:
        json_str = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_str}\n\n"
