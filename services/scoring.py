"""LLM-based hotel scoring using Google Gemini."""

import asyncio
import json
from typing import Any, AsyncIterator, TypedDict

from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings


class HotelScore(BaseModel):
    hotel_id: str
    score: int
    top_reasons: list[str]
    score_penalties: list[str]


class ScoringResponse(BaseModel):
    results: list[HotelScore]


class ScoringProgress(TypedDict):
    processed: int
    total: int
    batch: int
    total_batches: int


class ScoringStart(TypedDict):
    total_hotels: int
    total_batches: int
    batch_size: int
    estimated_tokens: int


class ScoringBatchStart(TypedDict):
    batch: int
    total_batches: int
    hotels_in_batch: int
    estimated_tokens: int
    prompt: str


class ScoringRetry(TypedDict):
    batch: int
    attempt: int
    max_attempts: int
    error: str


class ScoringError(TypedDict):
    message: str
    error_type: str
    batch: int | None


class ScoringResult(TypedDict):
    type: str  # "start", "batch_start", "progress", "retry", "error", or "done"
    start: ScoringStart | None
    batch_start: ScoringBatchStart | None
    progress: ScoringProgress | None
    retry: ScoringRetry | None
    error: ScoringError | None
    results: list[dict] | None


SCORING_PROMPT = """Score hotels for user preferences. Return JSON that matches the schema.

User: {user_preferences}

Hotels: {hotels_json}

Rules:
- Score 0-100.
- Return ALL hotels sorted by score desc.
- top_reasons: 2-3 short phrases (<=10 words each).
- score_penalties: up to 5 short facts, ordered by severity, explaining why the score is lower; empty list if none.
- Do not include markdown or extra text.
"""

DEFAULT_MODEL_NAME = "gemini-3-flash-preview"
DEFAULT_BATCH_SIZE = 25
DEFAULT_RETRIES = 3

CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    """Estimate token count for text (rough approximation)."""
    return len(text) // CHARS_PER_TOKEN


def _create_agent(model_name: str = DEFAULT_MODEL_NAME) -> Agent:
    """Create scoring agent with specified model."""
    from google.genai.types import ThinkingLevel

    settings = GoogleModelSettings(
        temperature=0.2,
        google_thinking_config={"thinking_level": ThinkingLevel.LOW},
    )
    model = GoogleModel(model_name)
    return Agent(model, output_type=ScoringResponse, model_settings=settings)


def prepare_hotel_for_llm(
    hotel: dict[str, Any],
    currency: str = "EUR",
    min_price: float | None = None,
    max_price: float | None = None,
) -> dict[str, Any]:
    """Prepare hotel data for LLM scoring with key information."""
    rates_info = []
    for rate in hotel.get("rates", []):
        pt = rate.get("payment_options", {}).get("payment_types", [])
        price_str = pt[0].get("show_amount") if pt else None

        if price_str is not None:
            try:
                price = float(price_str)
                if min_price is not None and price < min_price:
                    continue
                if max_price is not None and price > max_price:
                    continue
            except (ValueError, TypeError):
                pass

        if len(rates_info) >= 5:
            break

        meal_data = rate.get("meal_data", {})

        rate_info = {
            "room": rate.get("room_name", "")[:60],
            "price": f"{price_str} {currency}" if price_str else None,
            "meal": meal_data.get("value", rate.get("meal", "")),
            "has_breakfast": meal_data.get("has_breakfast", False),
        }

        cancel = None
        for p in pt:
            cp = p.get("cancellation_penalties", {})
            if cp.get("free_cancellation_before"):
                cancel = cp["free_cancellation_before"][:10]
                break
        if cancel:
            rate_info["free_cancel_before"] = cancel

        rates_info.append(rate_info)

    amenities = []
    for g in hotel.get("amenity_groups", []):
        for a in g.get("amenities", []):
            amenities.append(a.get("name", "") if isinstance(a, dict) else str(a))

    reviews = []
    hr = hotel.get("reviews", {})
    for r in (hr.get("reviews", []) if isinstance(hr, dict) else [])[:10]:
        reviews.append({
            "id": r.get("id"),
            "rating": r.get("rating"),
            "plus": (r.get("review_plus") or "")[:150],
            "minus": (r.get("review_minus") or "")[:150],
        })

    return {
        "hotel_id": hotel.get("id", ""),
        "name": hotel.get("name", ""),
        "stars": hotel.get("star_rating", 0),
        "kind": hotel.get("kind", ""),
        "address": hotel.get("address", ""),
        "description": hotel.get("description_struct", ""),
        "facts": hotel.get("facts", []),
        "serp_filters": hotel.get("serp_filters", []),
        "rates": rates_info,
        "amenities": amenities[:20],
        "reviews": reviews,
    }


def _build_prompt(hotels_data: list[dict], user_preferences: str) -> str:
    """Build scoring prompt for a batch of hotels."""
    return SCORING_PROMPT.format(
        user_preferences=user_preferences,
        hotels_json=json.dumps(hotels_data, ensure_ascii=False),
    )


async def score_hotels(
    hotels: list[dict],
    user_preferences: str,
    currency: str = "EUR",
    min_price: float | None = None,
    max_price: float | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    model_name: str = DEFAULT_MODEL_NAME,
    retries: int = DEFAULT_RETRIES,
) -> AsyncIterator[ScoringResult]:
    """
    Score hotels based on user preferences using LLM.

    Yields ScoringResult events: start, batch_start, retry, progress, error, done.
    """
    agent = _create_agent(model_name)

    hotels_for_llm = [
        prepare_hotel_for_llm(h, currency, min_price, max_price)
        for h in hotels
    ]

    total = len(hotels_for_llm)
    total_batches = (total + batch_size - 1) // batch_size

    all_prompts_text = ""
    for i in range(0, total, batch_size):
        batch = hotels_for_llm[i : i + batch_size]
        all_prompts_text += _build_prompt(batch, user_preferences)
    estimated_tokens = estimate_tokens(all_prompts_text)

    yield {
        "type": "start",
        "start": {
            "total_hotels": total,
            "total_batches": total_batches,
            "batch_size": batch_size,
            "estimated_tokens": estimated_tokens,
        },
        "batch_start": None,
        "progress": None,
        "retry": None,
        "error": None,
        "results": None,
    }

    all_results = []
    processed = 0

    for batch_num, i in enumerate(range(0, total, batch_size), 1):
        batch = hotels_for_llm[i : i + batch_size]
        prompt = _build_prompt(batch, user_preferences)
        batch_tokens = estimate_tokens(prompt)

        yield {
            "type": "batch_start",
            "start": None,
            "batch_start": {
                "batch": batch_num,
                "total_batches": total_batches,
                "hotels_in_batch": len(batch),
                "estimated_tokens": batch_tokens,
                "prompt": prompt,
            },
            "progress": None,
            "retry": None,
            "error": None,
            "results": None,
        }

        result = None
        last_error = None
        for attempt in range(retries):
            try:
                response = await agent.run(prompt)
                result = response.output
                break
            except (ValidationError, ValueError) as e:
                last_error = e
                if attempt < retries - 1:
                    yield {
                        "type": "retry",
                        "start": None,
                        "batch_start": None,
                        "progress": None,
                        "retry": {
                            "batch": batch_num,
                            "attempt": attempt + 1,
                            "max_attempts": retries,
                            "error": str(e)[:100],
                        },
                        "error": None,
                        "results": None,
                    }
                    await asyncio.sleep(1)
                    continue
            except Exception as e:
                yield {
                    "type": "error",
                    "start": None,
                    "batch_start": None,
                    "progress": None,
                    "retry": None,
                    "error": {
                        "message": str(e),
                        "error_type": type(e).__name__,
                        "batch": batch_num,
                    },
                    "results": None,
                }
                return

        if result:
            all_results.extend([h.model_dump() for h in result.results])

        processed += len(batch)

        yield {
            "type": "progress",
            "start": None,
            "batch_start": None,
            "progress": {
                "processed": processed,
                "total": total,
                "batch": batch_num,
                "total_batches": total_batches,
            },
            "retry": None,
            "error": None,
            "results": None,
        }

    all_results.sort(key=lambda x: x.get("score", 0), reverse=True)

    yield {
        "type": "done",
        "start": None,
        "batch_start": None,
        "progress": None,
        "retry": None,
        "error": None,
        "results": all_results,
    }
