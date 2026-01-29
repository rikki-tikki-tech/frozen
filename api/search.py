"""Hotel search streaming pipeline."""

from collections.abc import AsyncIterator

import httpx
from pydantic import ValidationError

from etg import ETGAPIError, ETGClient, ETGNetworkError
from services import (
    CONTENT_BATCH_SIZE,
    REVIEWS_BATCH_SIZE,
    batch_get_content,
    batch_get_reviews,
    combine_hotels_data,
    filter_reviews,
    finalize_scored_hotels,
    presort_hotels,
    sample_hotels,
    score_hotels,
    search_hotels,
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
    ScoringDoneEvent,
    ScoringStartEvent,
)
from .schemas import HotelSearchRequest

DEFAULT_PREFERENCES = "Лучшее соотношение цены и качества, хорошие отзывы, удобное расположение"
PRESORT_LIMIT = 100


async def search_stream(
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
        preferences = user_preferences or DEFAULT_PREFERENCES
        yield sse_event(ScoringStartEvent(
            total_hotels=len(top_hotels),
        ))

        scoring_result = await score_hotels(
            top_hotels,
            preferences,
            guests=guests,
            min_price=min_price_per_night,
            max_price=max_price_per_night,
            currency=currency,
        )

        if scoring_result["error"]:
            yield sse_event(ErrorEvent(
                error_type="ScoringError",
                error_message=scoring_result["error"],
            ))
            return

        yield sse_event(ScoringDoneEvent(
            scored_count=len(scoring_result["results"]),
            summary=scoring_result["summary"],
        ))

        # Finalize and yield results
        scored_hotels = finalize_scored_hotels(top_hotels, scoring_result["results"])
        yield sse_event(DoneEvent(total_scored=len(scored_hotels), hotels=scored_hotels))

    except ETGAPIError as e:
        yield sse_event(ErrorEvent(error_type="ETGAPIError", error_message=str(e)))
    except ETGNetworkError as e:
        yield sse_event(ErrorEvent(error_type="ETGNetworkError", error_message=str(e)))
    except httpx.HTTPError as e:
        yield sse_event(ErrorEvent(error_type="HTTPError", error_message=str(e)))
    except ValidationError as e:
        yield sse_event(ErrorEvent(error_type="ValidationError", error_message=str(e)))
