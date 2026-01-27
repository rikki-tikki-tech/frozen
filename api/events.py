"""SSE Event types for hotel search streaming."""

from typing import Any, Literal

from pydantic import BaseModel


# =============================================================================
# Phase 1: Search
# =============================================================================


class SearchStartEvent(BaseModel):
    """Search pipeline started — shows what we're looking for."""

    type: Literal["search_start"] = "search_start"
    phase: Literal["search"] = "search"
    message: str


class HotelsFoundEvent(BaseModel):
    """Hotels found after ETG search + filtering."""

    type: Literal["hotels_found"] = "hotels_found"
    phase: Literal["search"] = "search"
    total_available: int
    total_after_filter: int
    sampled: int | None = None  # if random sampling was applied
    message: str


# =============================================================================
# Phase 2: Content fetching
# =============================================================================


class ContentProgressEvent(BaseModel):
    """Per-batch progress of hotel content fetching."""

    type: Literal["content_progress"] = "content_progress"
    phase: Literal["content"] = "content"
    batch: int
    total_batches: int
    hotels_loaded: int
    total_hotels: int
    message: str


class ContentDoneEvent(BaseModel):
    """Content fetching completed."""

    type: Literal["content_done"] = "content_done"
    phase: Literal["content"] = "content"
    hotels_with_content: int
    total_hotels: int
    message: str


# =============================================================================
# Phase 3: Reviews fetching
# =============================================================================


class ReviewsProgressEvent(BaseModel):
    """Per-batch/language progress of reviews fetching."""

    type: Literal["reviews_progress"] = "reviews_progress"
    phase: Literal["reviews"] = "reviews"
    language: str
    batch: int
    total_batches: int
    hotels_loaded: int
    total_hotels: int
    message: str


class ReviewsSummaryEvent(BaseModel):
    """Reviews fetched and filtered — summary statistics."""

    type: Literal["reviews_summary"] = "reviews_summary"
    phase: Literal["reviews"] = "reviews"
    total_reviews_raw: int
    total_reviews_filtered: int
    hotels_with_reviews: int
    total_hotels: int
    positive_count: int
    neutral_count: int
    negative_count: int
    message: str


# =============================================================================
# Phase 4: Pre-scoring and selection
# =============================================================================


class PresortDoneEvent(BaseModel):
    """Pre-sorting completed — top hotels selected for LLM."""

    type: Literal["presort_done"] = "presort_done"
    phase: Literal["presort"] = "presort"
    input_hotels: int
    output_hotels: int
    min_prescore: float
    max_prescore: float
    message: str


# =============================================================================
# Phase 5: LLM Scoring (kept from original, enhanced with phase)
# =============================================================================


class ScoringStartEvent(BaseModel):
    """Scoring process started event."""

    type: Literal["scoring_start"] = "scoring_start"
    phase: Literal["scoring"] = "scoring"
    total_hotels: int
    total_batches: int
    batch_size: int
    estimated_tokens: int
    message: str


class ScoringBatchStartEvent(BaseModel):
    """Scoring batch started event."""

    type: Literal["scoring_batch_start"] = "scoring_batch_start"
    phase: Literal["scoring"] = "scoring"
    batch: int
    total_batches: int
    hotels_in_batch: int
    estimated_tokens: int
    message: str


class ScoringRetryEvent(BaseModel):
    """Scoring batch retry event."""

    type: Literal["scoring_retry"] = "scoring_retry"
    phase: Literal["scoring"] = "scoring"
    batch: int
    attempt: int
    max_attempts: int
    message: str


class ScoringProgressEvent(BaseModel):
    """Scoring progress event."""

    type: Literal["scoring_progress"] = "scoring_progress"
    phase: Literal["scoring"] = "scoring"
    processed: int
    total: int
    message: str


# =============================================================================
# Terminal events
# =============================================================================


class ErrorEvent(BaseModel):
    """Error event."""

    type: Literal["error"] = "error"
    error_type: str
    message: str
    batch: int | None = None


class DoneEvent(BaseModel):
    """Search completed event."""

    type: Literal["done"] = "done"
    total_scored: int
    hotels: list[dict[str, Any]]


SSEEvent = (
    SearchStartEvent
    | HotelsFoundEvent
    | ContentProgressEvent
    | ContentDoneEvent
    | ReviewsProgressEvent
    | ReviewsSummaryEvent
    | PresortDoneEvent
    | ScoringStartEvent
    | ScoringBatchStartEvent
    | ScoringRetryEvent
    | ScoringProgressEvent
    | ErrorEvent
    | DoneEvent
)
