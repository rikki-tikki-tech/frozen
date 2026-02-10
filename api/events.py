"""SSE Event types for hotel search streaming."""

from datetime import date
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel

from etg import GuestRoom
from utils import SSEMessage


class EventType(str, Enum):
    """Event types for SSE streaming."""

    # Phase 1: Search
    HOTEL_SEARCH_START = "hotel_search_start"
    HOTEL_SEARCH_DONE = "hotel_search_done"

    # Phase 2: Content
    BATCH_GET_CONTENT_START = "batch_get_content_start"
    BATCH_GET_CONTENT_DONE = "batch_get_content_done"

    # Phase 3: Reviews
    BATCH_GET_REVIEWS_START = "batch_get_reviews_start"
    BATCH_GET_REVIEWS_DONE = "batch_get_reviews_done"

    # Phase 4: Presort
    PRESORT_DONE = "presort_done"

    # Phase 5: Scoring
    SCORING_START = "scoring_start"
    SCORING_DONE = "scoring_done"

    # Terminal
    ERROR = "error"
    DONE = "done"


class SSEBaseEvent(BaseModel):
    """Base class for SSE payloads with bound event names."""

    event_type: ClassVar[EventType]


def sse_message(payload: "SSEBaseEvent") -> SSEMessage:
    """Wrap payload into SSE message using its bound event type."""
    return SSEMessage(event=payload.event_type.value, data=payload)


class HotelSearchStartEvent(SSEBaseEvent):
    """Hotel search started."""

    event_type: ClassVar[EventType] = EventType.HOTEL_SEARCH_START
    region_id: int
    checkin: date
    checkout: date
    guests: list[GuestRoom]
    residency: str
    currency: str | None = None
    language: str | None = None
    min_price_per_night: float | None = None
    max_price_per_night: float | None = None
    user_preferences: str | None = None


class HotelSearchDoneEvent(SSEBaseEvent):
    """Hotels found after ETG search + filtering."""

    event_type: ClassVar[EventType] = EventType.HOTEL_SEARCH_DONE
    total_available: int
    total_after_filter: int
    sampled: int | None = None


class BatchGetContentStartEvent(SSEBaseEvent):
    """Content fetching started."""

    event_type: ClassVar[EventType] = EventType.BATCH_GET_CONTENT_START
    total_hotels: int
    total_batches: int


class BatchGetContentDoneEvent(SSEBaseEvent):
    """Content fetching completed."""

    event_type: ClassVar[EventType] = EventType.BATCH_GET_CONTENT_DONE
    hotels_with_content: int
    total_hotels: int


class BatchGetReviewsStartEvent(SSEBaseEvent):
    """Reviews fetching started."""

    event_type: ClassVar[EventType] = EventType.BATCH_GET_REVIEWS_START
    total_hotels: int
    total_batches: int


class BatchGetReviewsDoneEvent(SSEBaseEvent):
    """Reviews fetching completed."""

    event_type: ClassVar[EventType] = EventType.BATCH_GET_REVIEWS_DONE
    hotels_with_reviews: int
    total_hotels: int


class PresortDoneEvent(SSEBaseEvent):
    """Pre-sorting completed."""

    event_type: ClassVar[EventType] = EventType.PRESORT_DONE
    input_hotels: int
    output_hotels: int


class ScoringStartEvent(SSEBaseEvent):
    """Scoring started."""

    event_type: ClassVar[EventType] = EventType.SCORING_START
    total_hotels: int


class ScoringDoneEvent(SSEBaseEvent):
    """Scoring completed."""

    event_type: ClassVar[EventType] = EventType.SCORING_DONE
    scored_count: int


class ErrorEvent(SSEBaseEvent):
    """Error event."""

    event_type: ClassVar[EventType] = EventType.ERROR
    error_type: str
    error_message: str
    batch: int | None = None


class DoneEvent(SSEBaseEvent):
    """Search completed with scored hotels."""

    event_type: ClassVar[EventType] = EventType.DONE
    total_scored: int
    hotels: list[dict[str, Any]]


SSEEvent = (
    HotelSearchStartEvent
    | HotelSearchDoneEvent
    | BatchGetContentStartEvent
    | BatchGetContentDoneEvent
    | BatchGetReviewsStartEvent
    | BatchGetReviewsDoneEvent
    | PresortDoneEvent
    | ScoringStartEvent
    | ScoringDoneEvent
    | ErrorEvent
    | DoneEvent
)
