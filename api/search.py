"""Hotel search streaming pipeline."""

import asyncio
import random
from collections.abc import AsyncIterator
from typing import Any, cast

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


async def search_stream(
    request: HotelSearchRequest,
    etg_client: AsyncETGClient,
) -> AsyncIterator[str]:
    """Execute the full hotel search pipeline, yielding SSE events."""
    city = request.city or f"регион {request.region_id}"
    dates = format_dates(request.checkin, request.checkout)
    guests_str = format_guests(request.guests)

    # Queue for events from callbacks (content/reviews progress)
    event_queue: asyncio.Queue[str] = asyncio.Queue()

    try:
        # =====================================================================
        # Phase 1: Search hotels
        # =====================================================================
        yield sse_event(SearchStartEvent(
            message=f"Ищу доступные номера: {city} · {dates} · {guests_str}",
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

        # Price filtering
        hotels = filter_hotels_by_price(
            hotels, request.min_price_per_night, request.max_price_per_night
        )
        hotels = filter_hotels_by_price(hotels, min_price_per_night=30.0)

        total_after_filter = len(hotels)

        # Random sampling if too many
        sampled: int | None = None
        if len(hotels) > 500:
            hotels = random.sample(hotels, 500)
            sampled = 500

        price_info = ""
        if request.min_price_per_night or request.max_price_per_night:
            parts = []
            if request.min_price_per_night:
                parts.append(f"от {request.min_price_per_night:.0f}")
            if request.max_price_per_night:
                parts.append(f"до {request.max_price_per_night:.0f}")
            price_info = f" (фильтр: {' '.join(parts)} за ночь)"

        found_msg = f"Найдено {len(hotels)} отелей из {total_hotels} доступных{price_info}"
        if sampled:
            found_msg += f", выбрано {sampled} для анализа"

        yield sse_event(HotelsFoundEvent(
            total_available=total_hotels,
            total_after_filter=total_after_filter,
            sampled=sampled,
            message=found_msg,
        ))

        # =====================================================================
        # Phase 2: Fetch hotel content
        # =====================================================================
        hids = [h["hid"] for h in hotels]

        async def on_content_progress(
            batch: int, total_batches: int, loaded: int, total: int,
        ) -> None:
            msg = f"Загрузка контента: {loaded}/{total} отелей (батч {batch}/{total_batches})"
            event_queue.put_nowait(sse_event(ContentProgressEvent(
                batch=batch,
                total_batches=total_batches,
                hotels_loaded=loaded,
                total_hotels=total,
                message=msg,
            )))

        content_map = await fetch_hotel_content_async(
            etg_client, hids, request.language or "ru",
            on_progress=on_content_progress,
        )

        # Flush queued content progress events
        while not event_queue.empty():
            yield event_queue.get_nowait()

        yield sse_event(ContentDoneEvent(
            hotels_with_content=len(content_map),
            total_hotels=len(hids),
            message=f"Загружен контент для {len(content_map)} из {len(hids)} отелей",
        ))

        for hotel in hotels:
            content = content_map.get(hotel["hid"])
            if content:
                hotel["content"] = content  # type: ignore[typeddict-unknown-key]

        # =====================================================================
        # Phase 3: Fetch and filter reviews
        # =====================================================================
        language = request.language or "ru"

        async def on_reviews_progress(
            lang: str, batch: int, total_batches: int, loaded: int, total: int,
        ) -> None:
            msg = f"Отзывы [{lang}]: {loaded}/{total} отелей (батч {batch}/{total_batches})"
            event_queue.put_nowait(sse_event(ReviewsProgressEvent(
                language=lang,
                batch=batch,
                total_batches=total_batches,
                hotels_loaded=loaded,
                total_hotels=total,
                message=msg,
            )))

        raw_reviews = await fetch_reviews_async(
            etg_client, hids, language,
            on_progress=on_reviews_progress,
        )

        # Flush queued reviews progress events
        while not event_queue.empty():
            yield event_queue.get_nowait()

        reviews_map = filter_reviews(raw_reviews)

        # Compute summary stats
        total_reviews_raw = sum(len(revs) for revs in raw_reviews.values())
        total_reviews_filtered = sum(
            len(rd["reviews"]) for rd in reviews_map.values()
        )
        hotels_with_reviews = len(reviews_map)
        total_positive = sum(rd["positive_count"] for rd in reviews_map.values())
        total_neutral = sum(rd["neutral_count"] for rd in reviews_map.values())
        total_negative = sum(rd["negative_count"] for rd in reviews_map.values())

        yield sse_event(ReviewsSummaryEvent(
            total_reviews_raw=total_reviews_raw,
            total_reviews_filtered=total_reviews_filtered,
            hotels_with_reviews=hotels_with_reviews,
            total_hotels=len(hids),
            positive_count=total_positive,
            neutral_count=total_neutral,
            negative_count=total_negative,
            message=(
                f"Обработано {total_reviews_raw} отзывов → {total_reviews_filtered} релевантных "
                f"({total_positive}+/{total_neutral}~/{total_negative}-) "
                f"для {hotels_with_reviews} отелей"
            ),
        ))

        for hotel in hotels:
            reviews = reviews_map.get(hotel["hid"])
            if reviews:
                hotel["reviews"] = reviews  # type: ignore[typeddict-unknown-key]

        # =====================================================================
        # Phase 4: Pre-scoring and selection for LLM
        # =====================================================================
        if request.user_preferences:
            preferences = request.user_preferences
        else:
            preferences = "Лучшее соотношение цены и качества, хорошие отзывы, удобное расположение"

        combined = []
        for hotel in hotels:
            combined.append({
                **hotel,
                **content_map.get(hotel["hid"], {}),
                "reviews": reviews_map.get(hotel["hid"], {}),
            })

        top_hotels = presort_hotels(
            combined, cast("dict[int, dict[str, Any]]", reviews_map), limit=100
        )

        # Compute prescore stats
        prescores = [h.get("prescore", 0.0) for h in top_hotels]
        min_ps = min(prescores) if prescores else 0.0
        max_ps = max(prescores) if prescores else 0.0

        yield sse_event(PresortDoneEvent(
            input_hotels=len(combined),
            output_hotels=len(top_hotels),
            min_prescore=round(min_ps, 1),
            max_prescore=round(max_ps, 1),
            message=(
                f"Предварительный отбор: {len(top_hotels)} лучших из {len(combined)} отелей "
                f"(пре-скор {min_ps:.0f}–{max_ps:.0f})"
            ),
        ))

        # =====================================================================
        # Phase 5: LLM Scoring
        # =====================================================================
        scoring_results: list[dict[str, Any]] = []
        async for result in score_hotels(
            top_hotels,
            preferences,
            currency=request.currency or "EUR",
            min_price=request.min_price_per_night,
            max_price=request.max_price_per_night,
        ):
            if result["type"] == "start" and result["start"] is not None:
                start = result["start"]
                h = start["total_hotels"]
                b = start["total_batches"]
                t = start["estimated_tokens"]
                yield sse_event(ScoringStartEvent(
                    total_hotels=h,
                    total_batches=b,
                    batch_size=start["batch_size"],
                    estimated_tokens=t,
                    message=f"AI-оценка {h} отелей: {b} батчей, ~{t:,} токенов",
                ))
            elif result["type"] == "batch_start" and result["batch_start"] is not None:
                bs = result["batch_start"]
                bn = bs["batch"]
                tb = bs["total_batches"]
                hb = bs["hotels_in_batch"]
                et = bs["estimated_tokens"]
                yield sse_event(ScoringBatchStartEvent(
                    batch=bn,
                    total_batches=tb,
                    hotels_in_batch=hb,
                    estimated_tokens=et,
                    message=f"Батч {bn}/{tb}: оцениваю {hb} отелей (~{et:,} токенов)",
                ))
            elif result["type"] == "retry" and result["retry"] is not None:
                retry = result["retry"]
                bn = retry["batch"]
                at = retry["attempt"]
                ma = retry["max_attempts"]
                yield sse_event(ScoringRetryEvent(
                    batch=bn,
                    attempt=at,
                    max_attempts=ma,
                    message=f"Повтор батча {bn}: попытка {at}/{ma}",
                ))
            elif result["type"] == "error" and result["error"] is not None:
                error = result["error"]
                yield sse_event(ErrorEvent(
                    error_type=error["error_type"],
                    message=error["message"],
                    batch=error["batch"],
                ))
                return
            elif result["type"] == "progress" and result["progress"] is not None:
                progress = result["progress"]
                yield sse_event(ScoringProgressEvent(
                    processed=progress["processed"],
                    total=progress["total"],
                    message=f"Оценено {progress['processed']} из {progress['total']} отелей",
                ))
            elif result["type"] == "done" and result["results"] is not None:
                scoring_results = result["results"]

        scoring_map = {s["hotel_id"]: s for s in scoring_results}
        for htl in top_hotels:
            score_data = scoring_map.get(htl["id"])
            if score_data:
                htl["scoring"] = score_data

        top_hotels.sort(key=lambda x: x.get("scoring", {}).get("score", 0), reverse=True)

        for htl in top_hotels:
            htl["ostrovok_url"] = get_ostrovok_url(
                hotel_id=htl["id"],
                hid=htl["hid"],
                city=request.city,
                country_code=request.country_code or "",
            )

        # =====================================================================
        # Phase 6: Done
        # =====================================================================
        yield sse_event(DoneEvent(
            total_scored=len(top_hotels),
            hotels=top_hotels,
        ))

    except Exception as e:
        yield sse_event(ErrorEvent(
            error_type=type(e).__name__,
            message=str(e),
        ))
