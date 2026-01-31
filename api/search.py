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
    filter_hotels_by_price,
    filter_reviews,
    finalize_scored_hotels,
    presort_hotels,
    sample_hotels,
    score_hotels,
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

        # Search hotels in region
        search_results = await etg_client.search_hotels_by_region(
            region_id=region_id,
            checkin=checkin.isoformat(),
            checkout=checkout.isoformat(),
            residency=residency,
            guests=guests,
            currency=currency,
            language=language,
            hotels_limit=request.hotels_limit,
        )
        all_hotels = search_results.get("hotels", [])
        total_available = search_results.get("total_hotels", len(all_hotels))

        # Filter by price
        filtered_hotels = filter_hotels_by_price(
            all_hotels, min_price_per_night, max_price_per_night
        )
        total_after_filter = len(filtered_hotels)

        sample_result = sample_hotels(filtered_hotels)
        hotels = sample_result["hotels"]
        sampled = sample_result["sampled"]
        yield sse_event(HotelSearchDoneEvent(
            total_available=total_available,
            total_after_filter=total_after_filter,
            sampled=sampled,
        ))

        # Early exit if no hotels found
        if not hotels:
            yield sse_event(DoneEvent(total_scored=0, hotels=[]))
            return

        # Phase 2: Fetch content
        hotel_ids = [hotel["hid"] for hotel in hotels]
        total_batches = (len(hotel_ids) + CONTENT_BATCH_SIZE - 1) // CONTENT_BATCH_SIZE
        yield sse_event(BatchGetContentStartEvent(
            total_hotels=len(hotel_ids),
            total_batches=total_batches,
        ))
        content_map = await batch_get_content(etg_client, hotel_ids, language)
        yield sse_event(BatchGetContentDoneEvent(
            hotels_with_content=len(content_map),
            total_hotels=len(hotel_ids),
        ))

        # Phase 3: Fetch reviews
        reviews_batch_count = (len(hotel_ids) + REVIEWS_BATCH_SIZE - 1) // REVIEWS_BATCH_SIZE
        yield sse_event(BatchGetReviewsStartEvent(
            total_hotels=len(hotel_ids),
            total_batches=reviews_batch_count,
        ))
        reviews_payload = await batch_get_reviews(etg_client, hotel_ids, language)
        reviews_map = filter_reviews(reviews_payload)
        yield sse_event(BatchGetReviewsDoneEvent(
            hotels_with_reviews=len(reviews_map),
            total_hotels=len(hotel_ids),
        ))

        # Phase 4: Presort
        combined_hotels = combine_hotels_data(hotels, content_map, reviews_map)
        top_hotels = presort_hotels(combined_hotels, reviews_map, limit=PRESORT_LIMIT)

        yield sse_event(PresortDoneEvent(
            input_hotels=len(combined_hotels),
            output_hotels=len(top_hotels),
        ))

        # Phase 5: LLM Scoring
        scoring_preferences = user_preferences or DEFAULT_PREFERENCES
        yield sse_event(ScoringStartEvent(
            total_hotels=len(top_hotels),
        ))

        scoring_result = await score_hotels(
            top_hotels,
            scoring_preferences,
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
