"""Hotel search streaming pipeline."""

import asyncio
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import httpx
from pydantic import ValidationError

from etg import AsyncETGClient, ETGAPIError, ETGNetworkError, Hotel, SearchParams
from services import (
    HotelReviewsFiltered,
    ScoringParams,
    fetch_hotel_content_async,
    fetch_reviews_async,
    filter_hotels_by_price,
    filter_reviews,
    get_ostrovok_url,
    presort_hotels,
    score_hotels,
)
from utils import format_dates, format_guests, sse_event

from .events import (
    ContentDoneEvent,
    ContentProgressEvent,
    DoneEvent,
    ErrorEvent,
    HotelsFoundEvent,
    PresortDoneEvent,
    ReviewsProgressEvent,
    ReviewsSummaryEvent,
    ScoringBatchStartEvent,
    ScoringProgressEvent,
    ScoringRetryEvent,
    ScoringStartEvent,
    SearchStartEvent,
)
from .schemas import HotelSearchRequest

MAX_HOTELS_FOR_ANALYSIS = 500
MIN_PRICE_FLOOR = 30.0
DEFAULT_PREFERENCES = "Лучшее соотношение цены и качества, хорошие отзывы, удобное расположение"
PRESORT_LIMIT = 100


@dataclass
class ReviewsStats:
    """Statistics about processed reviews."""

    total_raw: int
    total_filtered: int
    hotels_count: int
    positive: int
    neutral: int
    negative: int


def _build_price_filter_info(
    min_price: float | None,
    max_price: float | None,
) -> str:
    """Build price filter description string.

    Args:
        min_price: Minimum price per night.
        max_price: Maximum price per night.

    Returns:
        Formatted price filter string or empty string.
    """
    if not min_price and not max_price:
        return ""
    parts = []
    if min_price:
        parts.append(f"от {min_price:.0f}")
    if max_price:
        parts.append(f"до {max_price:.0f}")
    return f" (фильтр: {' '.join(parts)} за ночь)"


def _compute_reviews_stats(
    raw_reviews: dict[int, list[dict[str, Any]]],
    reviews_map: dict[int, HotelReviewsFiltered],
) -> ReviewsStats:
    """Compute summary statistics for reviews.

    Args:
        raw_reviews: Raw reviews by hotel ID.
        reviews_map: Filtered reviews by hotel ID.

    Returns:
        ReviewsStats with computed statistics.
    """
    return ReviewsStats(
        total_raw=sum(len(revs) for revs in raw_reviews.values()),
        total_filtered=sum(len(rd["reviews"]) for rd in reviews_map.values()),
        hotels_count=len(reviews_map),
        positive=sum(rd["positive_count"] for rd in reviews_map.values()),
        neutral=sum(rd["neutral_count"] for rd in reviews_map.values()),
        negative=sum(rd["negative_count"] for rd in reviews_map.values()),
    )


def _compute_prescore_stats(hotels: list[dict[str, Any]]) -> tuple[float, float]:
    """Compute min and max prescores from hotels.

    Args:
        hotels: List of hotels with prescore values.

    Returns:
        Tuple of (min_prescore, max_prescore).
    """
    prescores = [h.get("prescore", 0.0) for h in hotels]
    if not prescores:
        return 0.0, 0.0
    return min(prescores), max(prescores)


async def _run_scoring_phase(
    hotels: list[dict[str, Any]],
    preferences: str,
    params: ScoringParams,
) -> AsyncIterator[tuple[str | None, list[dict[str, Any]] | None]]:
    """Run LLM scoring phase, yielding SSE events and final results.

    Args:
        hotels: Hotels to score.
        preferences: User preferences for scoring.
        params: Scoring parameters.

    Yields:
        Tuple of (sse_event or None, results or None).
    """
    async for result in score_hotels(hotels, preferences, params):
        event: str | None = None

        if result["type"] == "start" and result["start"] is not None:
            start = result["start"]
            event = sse_event(ScoringStartEvent(
                total_hotels=start["total_hotels"],
                total_batches=start["total_batches"],
                batch_size=start["batch_size"],
                estimated_tokens=start["estimated_tokens"],
                message=(
                    f"AI-оценка {start['total_hotels']} отелей: "
                    f"{start['total_batches']} батчей, ~{start['estimated_tokens']:,} токенов"
                ),
            ))
        elif result["type"] == "batch_start" and result["batch_start"] is not None:
            bs = result["batch_start"]
            event = sse_event(ScoringBatchStartEvent(
                batch=bs["batch"],
                total_batches=bs["total_batches"],
                hotels_in_batch=bs["hotels_in_batch"],
                estimated_tokens=bs["estimated_tokens"],
                message=(
                    f"Батч {bs['batch']}/{bs['total_batches']}: "
                    f"оцениваю {bs['hotels_in_batch']} отелей (~{bs['estimated_tokens']:,} токенов)"
                ),
            ))
        elif result["type"] == "retry" and result["retry"] is not None:
            retry = result["retry"]
            event = sse_event(ScoringRetryEvent(
                batch=retry["batch"],
                attempt=retry["attempt"],
                max_attempts=retry["max_attempts"],
                message=(
                    f"Повтор батча {retry['batch']}: "
                    f"попытка {retry['attempt']}/{retry['max_attempts']}"
                ),
            ))
        elif result["type"] == "error" and result["error"] is not None:
            error = result["error"]
            event = sse_event(ErrorEvent(
                error_type=error["error_type"],
                message=error["message"],
                batch=error["batch"],
            ))
            yield event, None
            return
        elif result["type"] == "progress" and result["progress"] is not None:
            progress = result["progress"]
            event = sse_event(ScoringProgressEvent(
                processed=progress["processed"],
                total=progress["total"],
                message=f"Оценено {progress['processed']} из {progress['total']} отелей",
            ))
        elif result["type"] == "done" and result["results"] is not None:
            yield None, result["results"]
            return

        if event:
            yield event, None


async def _search_phase(
    request: HotelSearchRequest,
    etg_client: AsyncETGClient,
) -> tuple[list[Hotel], int, int, int | None]:
    """Execute Phase 1: Search and filter hotels.

    Args:
        request: Hotel search request.
        etg_client: ETG API client.

    Returns:
        Tuple of (hotels, total_available, total_after_filter, sampled_count).
    """
    search_params: SearchParams = {"guests": request.guests}
    if request.currency:
        search_params["currency"] = request.currency
    if request.language:
        search_params["language"] = request.language
    if request.hotels_limit:
        search_params["hotels_limit"] = request.hotels_limit

    results = await etg_client.search_hotels_by_region(
        region_id=request.region_id,
        checkin=request.checkin.isoformat(),
        checkout=request.checkout.isoformat(),
        residency=request.residency,
        params=search_params,
    )
    hotels: list[Hotel] = results.get("hotels", [])
    total_available = results.get("total_hotels", len(hotels))

    # Price filtering
    hotels = filter_hotels_by_price(
        hotels, request.min_price_per_night, request.max_price_per_night
    )
    hotels = filter_hotels_by_price(hotels, min_price_per_night=MIN_PRICE_FLOOR)
    total_after_filter = len(hotels)

    # Random sampling if too many
    sampled: int | None = None
    if len(hotels) > MAX_HOTELS_FOR_ANALYSIS:
        hotels = random.sample(hotels, MAX_HOTELS_FOR_ANALYSIS)
        sampled = MAX_HOTELS_FOR_ANALYSIS

    return hotels, total_available, total_after_filter, sampled


async def _content_phase(
    etg_client: AsyncETGClient,
    hotels: list[Hotel],
    language: str,
    event_queue: asyncio.Queue[str],
) -> dict[int, Any]:
    """Execute Phase 2: Fetch hotel content.

    Args:
        etg_client: ETG API client.
        hotels: List of hotels.
        language: Language code.
        event_queue: Queue for progress events.

    Returns:
        Content map by hotel ID.
    """
    hids = [h["hid"] for h in hotels]

    async def on_progress(batch: int, total_batches: int, loaded: int, total: int) -> None:
        msg = f"Загрузка контента: {loaded}/{total} отелей (батч {batch}/{total_batches})"
        event_queue.put_nowait(sse_event(ContentProgressEvent(
            batch=batch, total_batches=total_batches,
            hotels_loaded=loaded, total_hotels=total, message=msg,
        )))

    return await fetch_hotel_content_async(etg_client, hids, language, on_progress=on_progress)


async def _reviews_phase(
    etg_client: AsyncETGClient,
    hotels: list[Hotel],
    language: str,
    event_queue: asyncio.Queue[str],
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, HotelReviewsFiltered]]:
    """Execute Phase 3: Fetch and filter reviews.

    Args:
        etg_client: ETG API client.
        hotels: List of hotels.
        language: Language code.
        event_queue: Queue for progress events.

    Returns:
        Tuple of (raw_reviews, filtered_reviews_map).
    """
    hids = [h["hid"] for h in hotels]

    async def on_progress(
        lang: str, batch: int, total_batches: int, loaded: int, total: int,
    ) -> None:
        msg = f"Отзывы [{lang}]: {loaded}/{total} отелей (батч {batch}/{total_batches})"
        event_queue.put_nowait(sse_event(ReviewsProgressEvent(
            language=lang, batch=batch, total_batches=total_batches,
            hotels_loaded=loaded, total_hotels=total, message=msg,
        )))

    raw_reviews = await fetch_reviews_async(etg_client, hids, language, on_progress=on_progress)
    return raw_reviews, filter_reviews(raw_reviews)


def _finalize_hotels(
    top_hotels: list[dict[str, Any]],
    scoring_results: list[dict[str, Any]],
    city: str,
    country_code: str,
) -> None:
    """Apply scoring and URLs to hotels (in-place).

    Args:
        top_hotels: Hotels to finalize.
        scoring_results: LLM scoring results.
        city: City name.
        country_code: Country code.
    """
    scoring_map = {s["hotel_id"]: s for s in scoring_results}
    for htl in top_hotels:
        score_data = scoring_map.get(htl["id"])
        if score_data:
            htl["scoring"] = score_data
        htl["ostrovok_url"] = get_ostrovok_url(
            hotel_id=htl["id"], hid=htl["hid"], city=city, country_code=country_code,
        )
    top_hotels.sort(key=lambda x: x.get("scoring", {}).get("score", 0), reverse=True)


def _build_found_message(
    hotels_count: int,
    total_available: int,
    price_info: str,
    sampled: int | None,
) -> str:
    """Build the hotels found message.

    Args:
        hotels_count: Number of hotels after filtering.
        total_available: Total available hotels.
        price_info: Price filter info string.
        sampled: Number of sampled hotels if applicable.

    Returns:
        Formatted message string.
    """
    msg = f"Найдено {hotels_count} отелей из {total_available} доступных{price_info}"
    if sampled:
        msg += f", выбрано {sampled} для анализа"
    return msg


def _combine_hotels_data(
    hotels: list[Hotel],
    content_map: dict[int, Any],
    reviews_map: dict[int, HotelReviewsFiltered],
) -> list[dict[str, Any]]:
    """Combine hotel, content, and reviews data.

    Args:
        hotels: List of hotels.
        content_map: Content by hotel ID.
        reviews_map: Reviews by hotel ID.

    Returns:
        Combined hotel data list.
    """
    return [
        {
            **h,
            **content_map.get(h["hid"], {}),
            "reviews": reviews_map.get(h["hid"], {}),
        }
        for h in hotels
    ]


def _build_scoring_params(request: HotelSearchRequest) -> ScoringParams:
    """Build scoring parameters from request.

    Args:
        request: Hotel search request.

    Returns:
        Scoring parameters dictionary.
    """
    params: ScoringParams = {"currency": request.currency or "EUR"}
    if request.min_price_per_night:
        params["min_price"] = request.min_price_per_night
    if request.max_price_per_night:
        params["max_price"] = request.max_price_per_night
    return params


def _flush_queue(event_queue: asyncio.Queue[str]) -> list[str]:
    """Flush all events from queue.

    Args:
        event_queue: Queue to flush.

    Returns:
        List of events from queue.
    """
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return events


async def search_stream(
    request: HotelSearchRequest,
    etg_client: AsyncETGClient,
) -> AsyncIterator[str]:
    """Execute the full hotel search pipeline, yielding SSE events.

    Args:
        request: Hotel search request with filters and preferences.
        etg_client: ETG API client for hotel data.

    Yields:
        SSE-formatted event strings for streaming response.
    """
    city = request.city or f"регион {request.region_id}"
    language = request.language or "ru"
    event_queue: asyncio.Queue[str] = asyncio.Queue()

    try:
        # Phase 1: Search hotels
        dates = format_dates(request.checkin, request.checkout)
        guests_str = format_guests(request.guests)
        yield sse_event(SearchStartEvent(
            message=f"Ищу доступные номера: {city} · {dates} · {guests_str}",
        ))

        hotels, total_available, total_after_filter, sampled = await _search_phase(
            request, etg_client
        )
        price_info = _build_price_filter_info(
            request.min_price_per_night, request.max_price_per_night
        )
        yield sse_event(HotelsFoundEvent(
            total_available=total_available,
            total_after_filter=total_after_filter,
            sampled=sampled,
            message=_build_found_message(len(hotels), total_available, price_info, sampled),
        ))

        # Phase 2: Fetch content
        content_map = await _content_phase(etg_client, hotels, language, event_queue)
        for evt in _flush_queue(event_queue):
            yield evt
        yield sse_event(ContentDoneEvent(
            hotels_with_content=len(content_map),
            total_hotels=len(hotels),
            message=f"Загружен контент для {len(content_map)} из {len(hotels)} отелей",
        ))

        # Phase 3: Fetch reviews
        raw_reviews, reviews_map = await _reviews_phase(
            etg_client, hotels, language, event_queue
        )
        for evt in _flush_queue(event_queue):
            yield evt

        stats = _compute_reviews_stats(raw_reviews, reviews_map)
        yield sse_event(ReviewsSummaryEvent(
            total_reviews_raw=stats.total_raw,
            total_reviews_filtered=stats.total_filtered,
            hotels_with_reviews=stats.hotels_count,
            total_hotels=len(hotels),
            positive_count=stats.positive,
            neutral_count=stats.neutral,
            negative_count=stats.negative,
            message=(
                f"Обработано {stats.total_raw} отзывов → "
                f"{stats.total_filtered} релевантных "
                f"({stats.positive}+/{stats.neutral}~/{stats.negative}-) "
                f"для {stats.hotels_count} отелей"
            ),
        ))

        # Phase 4: Presort
        preferences = request.user_preferences or DEFAULT_PREFERENCES
        combined = _combine_hotels_data(hotels, content_map, reviews_map)
        top_hotels = presort_hotels(
            combined, cast("dict[int, dict[str, Any]]", reviews_map), limit=PRESORT_LIMIT
        )
        min_ps, max_ps = _compute_prescore_stats(top_hotels)

        yield sse_event(PresortDoneEvent(
            input_hotels=len(combined),
            output_hotels=len(top_hotels),
            min_prescore=round(min_ps, 1),
            max_prescore=round(max_ps, 1),
            message=(
                f"Предварительный отбор: {len(top_hotels)} лучших "
                f"из {len(combined)} отелей (пре-скор {min_ps:.0f}–{max_ps:.0f})"
            ),
        ))

        # Phase 5: LLM Scoring
        scoring_results: list[dict[str, Any]] = []
        scoring_params = _build_scoring_params(request)
        async for event, results in _run_scoring_phase(top_hotels, preferences, scoring_params):
            if event:
                yield event
            if results is not None:
                scoring_results = results

        # Finalize and yield results
        _finalize_hotels(top_hotels, scoring_results, request.city, request.country_code or "")
        yield sse_event(DoneEvent(total_scored=len(top_hotels), hotels=top_hotels))

    except ETGAPIError as e:
        yield sse_event(ErrorEvent(error_type="ETGAPIError", message=str(e)))
    except ETGNetworkError as e:
        yield sse_event(ErrorEvent(error_type="ETGNetworkError", message=str(e)))
    except httpx.HTTPError as e:
        yield sse_event(ErrorEvent(error_type="HTTPError", message=str(e)))
    except ValidationError as e:
        yield sse_event(ErrorEvent(error_type="ValidationError", message=str(e)))
