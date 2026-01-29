"""SSE Event types for hotel search streaming."""

from datetime import date
from enum import Enum

from pydantic import BaseModel

from etg import GuestRoom
from services import HotelScored


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
    SCORING_BATCH_START = "scoring_batch_start"
    SCORING_RETRY = "scoring_retry"
    SCORING_PROGRESS = "scoring_progress"

    # Terminal
    ERROR = "error"
    DONE = "done"


class HotelSearchStartEvent(BaseModel):
    """Hotel search started."""

    type: EventType = EventType.HOTEL_SEARCH_START
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


class HotelSearchDoneEvent(BaseModel):
    """Hotels found after ETG search + filtering."""

    type: EventType = EventType.HOTEL_SEARCH_DONE
    total_available: int
    total_after_filter: int
    sampled: int | None = None


class BatchGetContentStartEvent(BaseModel):
    """Content fetching started."""

    type: EventType = EventType.BATCH_GET_CONTENT_START
    total_hotels: int
    total_batches: int


class BatchGetContentDoneEvent(BaseModel):
    """Content fetching completed."""

    type: EventType = EventType.BATCH_GET_CONTENT_DONE
    hotels_with_content: int
    total_hotels: int


class BatchGetReviewsStartEvent(BaseModel):
    """Reviews fetching started."""

    type: EventType = EventType.BATCH_GET_REVIEWS_START
    total_hotels: int
    total_batches: int


class BatchGetReviewsDoneEvent(BaseModel):
    """Reviews fetching completed."""

    type: EventType = EventType.BATCH_GET_REVIEWS_DONE
    hotels_with_reviews: int
    total_hotels: int


class PresortDoneEvent(BaseModel):
    """Pre-sorting completed."""

    type: EventType = EventType.PRESORT_DONE
    input_hotels: int
    output_hotels: int


class ScoringStartEvent(BaseModel):
    """Scoring started."""

    type: EventType = EventType.SCORING_START
    total_hotels: int
    total_batches: int
    batch_size: int
    estimated_tokens: int


class ScoringBatchStartEvent(BaseModel):
    """Scoring batch started."""

    type: EventType = EventType.SCORING_BATCH_START
    batch: int
    total_batches: int
    hotels_in_batch: int
    estimated_tokens: int


class ScoringRetryEvent(BaseModel):
    """Scoring batch retry."""

    type: EventType = EventType.SCORING_RETRY
    batch: int
    attempt: int
    max_attempts: int


class ScoringProgressEvent(BaseModel):
    """Scoring progress."""

    type: EventType = EventType.SCORING_PROGRESS
    processed: int
    total: int


class ErrorEvent(BaseModel):
    """Error event."""

    type: EventType = EventType.ERROR
    error_type: str
    error_message: str
    batch: int | None = None


class DoneEvent(BaseModel):
    """Search completed."""

    type: EventType = EventType.DONE
    total_scored: int
    hotels: list[HotelScored]


SSEEvent = (
    HotelSearchStartEvent
    | HotelSearchDoneEvent
    | BatchGetContentStartEvent
    | BatchGetContentDoneEvent
    | BatchGetReviewsStartEvent
    | BatchGetReviewsDoneEvent
    | PresortDoneEvent
    | ScoringStartEvent
    | ScoringBatchStartEvent
    | ScoringRetryEvent
    | ScoringProgressEvent
    | ErrorEvent
    | DoneEvent
)
