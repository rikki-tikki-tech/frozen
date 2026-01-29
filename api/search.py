"""Hotel search streaming pipeline."""

from collections.abc import AsyncIterator

import httpx
from pydantic import ValidationError

from etg import ETGAPIError, ETGClient, ETGNetworkError
from services import (
    CONTENT_BATCH_SIZE,
    REVIEWS_BATCH_SIZE,
    HotelScoreDict,
    ScoringResult,
    batch_get_content,
    batch_get_reviews,
    combine_hotels_data,
    filter_reviews,
    finalize_scored_hotels,
    presort_hotels,
    sample_hotels,
    score_hotels,
    search_hotels,
    summarize_results,
)
from utils import sse_event

from .events import (
    BatchGetContentDoneEvent,
    BatchGetContentStartEvent,
    BatchGetReviewsDoneEvent,
    BatchGetReviewsStartEvent,
    DoneEvent,
    ErrorEvent,
    HotelSearchDoneEvent,
    HotelSearchStartEvent,
    PresortDoneEvent,
    ScoringBatchStartEvent,
    ScoringProgressEvent,
    ScoringRetryEvent,
    ScoringStartEvent,
    SummaryDoneEvent,
    SummaryStartEvent,
)
from .schemas import HotelSearchRequest

DEFAULT_PREFERENCES = "Лучшее соотношение цены и качества, хорошие отзывы, удобное расположение"
PRESORT_LIMIT = 100


def _scoring_result_to_sse(result: ScoringResult) -> str | None:
    """Convert scoring result to SSE event string."""
    if result["type"] == "start" and result["start"] is not None:
        start = result["start"]
        return sse_event(ScoringStartEvent(
            total_hotels=start["total_hotels"],
            total_batches=start["total_batches"],
            batch_size=start["batch_size"],
            estimated_tokens=start["estimated_tokens"],
        ))
    if result["type"] == "batch_start" and result["batch_start"] is not None:
        bs = result["batch_start"]
        return sse_event(ScoringBatchStartEvent(
            batch=bs["batch"],
            total_batches=bs["total_batches"],
            hotels_in_batch=bs["hotels_in_batch"],
            estimated_tokens=bs["estimated_tokens"],
        ))
    if result["type"] == "retry" and result["retry"] is not None:
        retry = result["retry"]
        return sse_event(ScoringRetryEvent(
            batch=retry["batch"],
            attempt=retry["attempt"],
            max_attempts=retry["max_attempts"],
        ))
    if result["type"] == "error" and result["error"] is not None:
        error = result["error"]
        return sse_event(ErrorEvent(
            error_type=error["error_type"],
            error_message=error["message"],
            batch=error["batch"],
        ))
    if result["type"] == "progress" and result["progress"] is not None:
        progress = result["progress"]
        return sse_event(ScoringProgressEvent(
            processed=progress["processed"],
            total=progress["total"],
        ))
    return None


async def search_stream(  # noqa: PLR0915
    request: HotelSearchRequest,
    etg_client: ETGClient,
) -> AsyncIterator[str]:
    """Execute the full hotel search pipeline, yielding SSE events."""
    # Extract request fields
    region_id = request.region_id
    checkin = request.checkin
    checkout = request.checkout
    guests = request.guests
    residency = request.residency
    currency = request.currency
    language = request.language or "ru"
    min_price_per_night = request.min_price_per_night
    max_price_per_night = request.max_price_per_night
    user_preferences = request.user_preferences

    try:
        # Phase 1: Search hotels
        yield sse_event(HotelSearchStartEvent(
            region_id=region_id,
            checkin=checkin,
            checkout=checkout,
            guests=guests,
            residency=residency,
            currency=currency,
            language=language,
            min_price_per_night=min_price_per_night,
            max_price_per_night=max_price_per_night,
            user_preferences=user_preferences,
        ))

        search_result = await search_hotels(
            client=etg_client,
            region_id=region_id,
            checkin=checkin.isoformat(),
            checkout=checkout.isoformat(),
            residency=residency,
            guests=guests,
            currency=currency,
            language=language,
            hotels_limit=request.hotels_limit,
            min_price=min_price_per_night,
            max_price=max_price_per_night,
        )
        total_available = search_result["total_available"]
        total_after_filter = search_result["total_after_filter"]

        sample_result = sample_hotels(search_result["hotels"])
        hotels = sample_result["hotels"]
        sampled = sample_result["sampled"]
        yield sse_event(HotelSearchDoneEvent(
            total_available=total_available,
            total_after_filter=total_after_filter,
            sampled=sampled,
        ))

        # Phase 2: Fetch content
        hids = [h["hid"] for h in hotels]
        total_batches = (len(hids) + CONTENT_BATCH_SIZE - 1) // CONTENT_BATCH_SIZE
        yield sse_event(BatchGetContentStartEvent(
            total_hotels=len(hids),
            total_batches=total_batches,
        ))
        content_result = await batch_get_content(etg_client, hids, language)
        yield sse_event(BatchGetContentDoneEvent(
            hotels_with_content=content_result["total_loaded"],
            total_hotels=content_result["total_requested"],
        ))

        # Phase 3: Fetch reviews
        reviews_batches = (len(hids) + REVIEWS_BATCH_SIZE - 1) // REVIEWS_BATCH_SIZE
        yield sse_event(BatchGetReviewsStartEvent(
            total_hotels=len(hids),
            total_batches=reviews_batches,
        ))
        raw_reviews = await batch_get_reviews(etg_client, hids, language)
        reviews_map = filter_reviews(raw_reviews)
        yield sse_event(BatchGetReviewsDoneEvent(
            hotels_with_reviews=len(reviews_map),
            total_hotels=len(hids),
        ))

        # Phase 4: Presort
        combined = combine_hotels_data(hotels, content_result["content"], reviews_map)
        top_hotels = presort_hotels(combined, reviews_map, limit=PRESORT_LIMIT)

        yield sse_event(PresortDoneEvent(
            input_hotels=len(combined),
            output_hotels=len(top_hotels),
        ))

        # Phase 5: LLM Scoring
        scoring_results: list[HotelScoreDict] = []
        preferences = user_preferences or DEFAULT_PREFERENCES
        async for result in score_hotels(top_hotels, preferences):
            event = _scoring_result_to_sse(result)
            if event:
                yield event
            if result["type"] == "error":
                break
            if result["type"] == "done" and result["results"] is not None:
                scoring_results = result["results"]

        # Finalize scored hotels
        scored_hotels = finalize_scored_hotels(top_hotels, scoring_results)

        # Phase 6: Summary
        yield sse_event(SummaryStartEvent(top_hotels_count=min(10, len(scored_hotels))))
        summary_result = await summarize_results(scored_hotels, preferences)
        if summary_result["summary"] is not None:
            yield sse_event(SummaryDoneEvent(summary=summary_result["summary"]))

        # Done
        yield sse_event(DoneEvent(total_scored=len(scored_hotels), hotels=scored_hotels))

    except ETGAPIError as e:
        yield sse_event(ErrorEvent(error_type="ETGAPIError", error_message=str(e)))
    except ETGNetworkError as e:
        yield sse_event(ErrorEvent(error_type="ETGNetworkError", error_message=str(e)))
    except httpx.HTTPError as e:
        yield sse_event(ErrorEvent(error_type="HTTPError", error_message=str(e)))
    except ValidationError as e:
        yield sse_event(ErrorEvent(error_type="ValidationError", error_message=str(e)))
