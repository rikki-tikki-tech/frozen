"""SSE Event types for hotel search streaming."""

from typing import Any, Literal

from pydantic import BaseModel


class StatusEvent(BaseModel):
    """Status update event."""

    type: Literal["status"] = "status"
    message: str


class ScoringStartEvent(BaseModel):
    """Scoring process started event."""

    type: Literal["scoring_start"] = "scoring_start"
    total_hotels: int
    total_batches: int
    batch_size: int
    estimated_tokens: int
    message: str


class ScoringBatchStartEvent(BaseModel):
    """Scoring batch started event."""

    type: Literal["scoring_batch_start"] = "scoring_batch_start"
    batch: int
    total_batches: int
    hotels_in_batch: int
    estimated_tokens: int
    message: str


class ScoringRetryEvent(BaseModel):
    """Scoring batch retry event."""

    type: Literal["scoring_retry"] = "scoring_retry"
    batch: int
    attempt: int
    max_attempts: int
    message: str


class ScoringProgressEvent(BaseModel):
    """Scoring progress event."""

    type: Literal["scoring_progress"] = "scoring_progress"
    processed: int
    total: int
    message: str


class ErrorEvent(BaseModel):
    """Error event."""

    type: Literal["error"] = "error"
    error_type: str
    message: str
    batch: int | None = None


class DoneEvent(BaseModel):
    """Search completed event."""

    type: Literal["done"] = "done"
    hotels: list[dict[str, Any]]


SSEEvent = (
    StatusEvent
    | ScoringStartEvent
    | ScoringBatchStartEvent
    | ScoringRetryEvent
    | ScoringProgressEvent
    | ErrorEvent
    | DoneEvent
)
