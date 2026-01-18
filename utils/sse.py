import json
from typing import Any

from pydantic import BaseModel


def sse_event(data: BaseModel | dict[str, Any]) -> str:
    """Format data as Server-Sent Event."""
    if isinstance(data, BaseModel):
        json_str = data.model_dump_json()
    else:
        json_str = json.dumps(data, ensure_ascii=False)
    return f"data: {json_str}\n\n"
