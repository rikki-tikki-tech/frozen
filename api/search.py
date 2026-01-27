"""Hotel search streaming pipeline."""

import random
from typing import AsyncIterator

from etg import AsyncETGClient, Hotel
from services import (
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
    DoneEvent,
    ErrorEvent,
    ScoringBatchStartEvent,
    ScoringProgressEvent,
    ScoringRetryEvent,
    ScoringStartEvent,
    StatusEvent,
)
from .schemas import HotelSearchRequest


async def search_stream(
    request: HotelSearchRequest,
    etg_client: AsyncETGClient,
) -> AsyncIterator[str]:
    """Execute the full hotel search pipeline, yielding SSE events."""
    city = request.city or f"регион {request.region_id}"
    dates = format_dates(request.checkin, request.checkout)
    guests_str = format_guests(request.guests)

    try:
        # 1. Searching
        yield sse_event(StatusEvent(
            message=f"Собираю варианты с доступными номерами в {city} · {dates} · {guests_str}",
        ))

        results = await etg_client.search_hotels_by_region(
            region_id=request.region_id,
            checkin=request.checkin.isoformat(),
            checkout=request.checkout.isoformat(),
            residency=request.residency,
            guests=request.guests,
            currency=request.currency,
            language=request.language,
            hotels_limit=request.hotels_limit,
        )
        hotels: list[Hotel] = results.get("hotels", [])
        total_hotels: int = results.get("total_hotels", len(hotels))

        # 2. Filter by price
        hotels = filter_hotels_by_price(hotels, request.min_price_per_night, request.max_price_per_night)

        # 2.1. Remove very cheap hotels (< 30 EUR per night)
        hotels = filter_hotels_by_price(hotels, min_price_per_night=30.0)

        # 2.2. Limit to 500 random hotels if more than that
        if len(hotels) > 500:
            hotels = random.sample(hotels, 500)

        # 3. Found hotels
        yield sse_event(StatusEvent(
            message=f"Найдено {len(hotels)} отелей" + (f" (из {total_hotels})" if len(hotels) != total_hotels else ""),
        ))

        # 4. Fetching content
        yield sse_event(StatusEvent(
            message="Получаю дополнительную информацию об отелях",
        ))

        hids = [h["hid"] for h in hotels]
        content_map = await fetch_hotel_content_async(etg_client, hids, request.language or "ru")

        for hotel in hotels:
            content = content_map.get(hotel["hid"])
            if content:
                hotel["content"] = content

        # 5. Fetching reviews
        yield sse_event(StatusEvent(
            message="Получаю отзывы об отелях",
        ))

        language = request.language or "ru"
        raw_reviews = await fetch_reviews_async(etg_client, hids, language)
        reviews_map = filter_reviews(raw_reviews)

        for hotel in hotels:
            reviews = reviews_map.get(hotel["hid"])
            if reviews:
                hotel["reviews"] = reviews

        # 6. Scoring
        if request.user_preferences:
            yield sse_event(StatusEvent(
                message="Оцениваю отели по вашим предпочтениям",
            ))
            preferences = request.user_preferences
        else:
            yield sse_event(StatusEvent(
                message="Оцениваю отели",
            ))
            preferences = "Лучшее соотношение цены и качества, хорошие отзывы, удобное расположение"

        combined = []
        for hotel in hotels:
            combined.append({
                **hotel,
                **content_map.get(hotel["hid"], {}),
                "reviews": reviews_map.get(hotel["hid"], {}),
            })

        top_hotels = presort_hotels(combined, reviews_map, limit=100)

        scoring_results = []
        async for result in score_hotels(
            top_hotels,
            preferences,
            currency=request.currency or "EUR",
            min_price=request.min_price_per_night,
            max_price=request.max_price_per_night,
        ):
            if result["type"] == "start":
                start = result["start"]
                yield sse_event(ScoringStartEvent(
                    total_hotels=start["total_hotels"],
                    total_batches=start["total_batches"],
                    batch_size=start["batch_size"],
                    estimated_tokens=start["estimated_tokens"],
                    message=f"Оцениваю {start['total_hotels']} отелей ({start['total_batches']} батчей, ~{start['estimated_tokens']:,} токенов)",
                ))
            elif result["type"] == "batch_start":
                bs = result["batch_start"]
                yield sse_event(ScoringBatchStartEvent(
                    batch=bs["batch"],
                    total_batches=bs["total_batches"],
                    hotels_in_batch=bs["hotels_in_batch"],
                    estimated_tokens=bs["estimated_tokens"],
                    message=f"Батч {bs['batch']}/{bs['total_batches']}: {bs['hotels_in_batch']} отелей, ~{bs['estimated_tokens']:,} токенов",
                ))
            elif result["type"] == "retry":
                retry = result["retry"]
                yield sse_event(ScoringRetryEvent(
                    batch=retry["batch"],
                    attempt=retry["attempt"],
                    max_attempts=retry["max_attempts"],
                    message=f"Повторная попытка батча {retry['batch']}: {retry['attempt']}/{retry['max_attempts']}",
                ))
            elif result["type"] == "error":
                error = result["error"]
                yield sse_event(ErrorEvent(
                    error_type=error["error_type"],
                    message=error["message"],
                    batch=error["batch"],
                ))
                return
            elif result["type"] == "progress":
                progress = result["progress"]
                yield sse_event(ScoringProgressEvent(
                    processed=progress["processed"],
                    total=progress["total"],
                    message=f"Оценено {progress['processed']} из {progress['total']} отелей",
                ))
            elif result["type"] == "done":
                scoring_results = result["results"]

        scoring_map = {s["hotel_id"]: s for s in scoring_results}
        for hotel in top_hotels:
            score_data = scoring_map.get(hotel["id"])
            if score_data:
                hotel["scoring"] = score_data

        top_hotels.sort(key=lambda h: h.get("scoring", {}).get("score", 0), reverse=True)

        for hotel in top_hotels:
            hotel["ostrovok_url"] = get_ostrovok_url(
                hotel_id=hotel["id"],
                hid=hotel["hid"],
                city=request.city,
                country_code=request.country_code or "",
            )

        # 7. Done
        yield sse_event(DoneEvent(
            hotels=top_hotels,
        ))

    except Exception as e:
        yield sse_event(ErrorEvent(
            error_type=type(e).__name__,
            message=str(e),
        ))
