import os
import random
from typing import AsyncIterator

from datetime import date

from dotenv import load_dotenv
from etg_client import AsyncETGClient, GuestRoom, Hotel, Region
from events import (
    StatusEvent,
    ScoringStartEvent,
    ScoringBatchStartEvent,
    ScoringRetryEvent,
    ScoringProgressEvent,
    ErrorEvent,
    DoneEvent,
)
from hotels import fetch_hotel_content_async, filter_hotels_by_price, get_ostrovok_url, presort_hotels
from reviews import fetch_reviews_async, filter_reviews
from scoring import score_hotels
from utils import format_dates, format_guests, sse_event
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

load_dotenv()

ETG_KEY_ID = os.environ["ETG_KEY_ID"]
ETG_API_KEY = os.environ["ETG_API_KEY"]
ETG_REQUEST_TIMEOUT = 30.0

etg_client = AsyncETGClient(ETG_KEY_ID, ETG_API_KEY, timeout=ETG_REQUEST_TIMEOUT)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://34.118.32.192"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "Hello World"}


# =============================================================================
# Region Suggest API
# =============================================================================


class RegionItem(BaseModel):
    """Регион из результатов поиска."""
    id: int = Field(description="ID региона")
    name: str = Field(description="Название региона")
    type: str = Field(description="Тип региона (City, Country, Airport и т.д.)")
    country_code: str = Field(description="Код страны (ISO 3166-1 alpha-2)")


class RegionSuggestResponse(BaseModel):
    """Ответ на запрос поиска региона."""
    query: str = Field(description="Исходный поисковый запрос")
    regions: list[RegionItem] = Field(description="Все найденные регионы")
    city: RegionItem | None = Field(description="Первый найденный город (или None)")


@app.get("/regions/suggest", response_model=RegionSuggestResponse)
async def suggest_regions(
    query: str = Query(min_length=1, description="Поисковый запрос (название города)"),
    language: str = Query(default="ru", pattern=r"^[a-z]{2}$", description="Код языка (ISO 639-1)"),
) -> RegionSuggestResponse:
    """
    Поиск региона по названию.

    Возвращает список регионов и отдельно первый найденный город.
    """
    raw_regions: list[Region] = await etg_client.suggest_region(query, language)

    regions = [
        RegionItem(
            id=r["id"],
            name=r["name"],
            type=r["type"],
            country_code=r.get("country_code", ""),
        )
        for r in raw_regions
    ]

    # Найти первый город
    city = next((r for r in regions if r.type == "City"), None)

    return RegionSuggestResponse(
        query=query,
        regions=regions,
        city=city,
    )


class HotelSearchRequest(BaseModel):
    region_id: int = Field(gt=0, description="ID региона поиска")
    city: str = Field(min_length=1, description="Название города")
    checkin: date = Field(description="Дата заезда")
    checkout: date = Field(description="Дата выезда")
    guests: list[GuestRoom] = Field(min_length=1, description="Количество гостей")
    residency: str = Field(pattern=r"^[a-z]{2}$", description="Код страны проживания (ISO 3166-1 alpha-2)")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$", description="Код валюты (ISO 4217)")
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$", description="Код языка (ISO 639-1)")
    hotels_limit: int | None = Field(default=None, gt=0, le=1000, description="Лимит отелей в результате")
    min_price_per_night: float | None = Field(default=None, gt=0, description="Минимальная цена за ночь")
    max_price_per_night: float | None = Field(default=None, gt=0, description="Максимальная цена за ночь")
    user_preferences: str | None = Field(default=None, description="Предпочтения пользователя для AI-скоринга")
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$", description="Код страны (ISO 3166-1 alpha-2) для ссылок на Островок")

    @model_validator(mode="after")
    def validate_checkout_after_checkin(self) -> "HotelSearchRequest":
        if self.checkout <= self.checkin:
            raise ValueError("Дата выезда должна быть позже даты заезда")
        return self


async def _search_stream(request: HotelSearchRequest) -> AsyncIterator[str]:
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

        # Merge content into hotels
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

        # Merge reviews into hotels
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

        # Combine hotels with content and reviews
        combined = []
        for hotel in hotels:
            combined.append({
                **hotel,
                **content_map.get(hotel["hid"], {}),
                "reviews": reviews_map.get(hotel["hid"], {}),
            })

        # Pre-sort and limit to top 100 for LLM scoring
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

        # Merge scoring into top_hotels
        scoring_map = {s["hotel_id"]: s for s in scoring_results}
        for hotel in top_hotels:
            score_data = scoring_map.get(hotel["id"])
            if score_data:
                hotel["scoring"] = score_data

        # Sort top_hotels by score
        top_hotels.sort(key=lambda h: h.get("scoring", {}).get("score", 0), reverse=True)

        # Add Ostrovok URLs
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


@app.post("/hotels/search/stream")
async def stream_hotels_search(request: HotelSearchRequest) -> StreamingResponse:
    return StreamingResponse(
        _search_stream(request),
        media_type="text/event-stream",
    )
