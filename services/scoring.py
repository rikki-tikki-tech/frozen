"""LLM-based hotel scoring using Google Gemini or Anthropic Claude."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, TypedDict

import httpx
from google.genai.types import ThinkingLevel
from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

from config import SCORING_MODEL


class HotelScore(BaseModel):
    """Individual hotel score from LLM evaluation."""

    hotel_id: str
    score: int
    top_reasons: list[str]
    score_penalties: list[str]


class ScoringResponse(BaseModel):
    """LLM response containing all hotel scores."""

    results: list[HotelScore]


class ScoringProgress(TypedDict):
    """Progress update during scoring process."""

    processed: int
    total: int
    batch: int
    total_batches: int


class ScoringStart(TypedDict):
    """Initial scoring metadata sent at start."""

    total_hotels: int
    total_batches: int
    batch_size: int
    estimated_tokens: int


class ScoringBatchStart(TypedDict):
    """Metadata sent at the start of each batch."""

    batch: int
    total_batches: int
    hotels_in_batch: int
    estimated_tokens: int
    prompt: str


class ScoringRetry(TypedDict):
    """Retry information when a batch fails."""

    batch: int
    attempt: int
    max_attempts: int
    error: str


class ScoringError(TypedDict):
    """Error information when scoring fails."""

    message: str
    error_type: str
    batch: int | None


class ScoringResult(TypedDict):
    """Union result type for scoring events.

    Contains one of: start, batch_start, progress, retry, error, or done events.
    """

    type: str  # "start", "batch_start", "progress", "retry", "error", or "done"
    start: ScoringStart | None
    batch_start: ScoringBatchStart | None
    progress: ScoringProgress | None
    retry: ScoringRetry | None
    error: ScoringError | None
    results: list[dict[str, Any]] | None


class ScoringParams(TypedDict, total=False):
    """Optional scoring parameters.

    All fields are optional with sensible defaults.
    """

    currency: str
    min_price: float
    max_price: float
    batch_size: int
    model_name: str
    retries: int


SCORING_PROMPT = """Score hotels for user preferences. Return JSON that matches the schema.

User: {user_preferences}

Hotels: {hotels_json}

Rules:
- Score 0-100.
- Return ALL hotels sorted by score desc.
- top_reasons: 2-3 short phrases (<=10 words each).
- score_penalties: up to 5 facts explaining why score is lower; empty if none.
- Do not include markdown or extra text.
"""

DEFAULT_BATCH_SIZE = 25
DEFAULT_RETRIES = 3

CHARS_PER_TOKEN = 3

MAX_RATES_PER_HOTEL = 5
MAX_REVIEWS_PER_HOTEL = 10
MAX_AMENITIES_PER_HOTEL = 20
REVIEW_TEXT_MAX_LENGTH = 150

ANTHROPIC_MODELS = {"claude-haiku-4-5", "claude-sonnet-4", "claude-opus-4"}


def _get_default_model() -> str:
    """Get the default scoring model from configuration."""
    return SCORING_MODEL


def _is_anthropic_model(model_name: str) -> bool:
    return model_name.startswith("claude-")


def estimate_tokens(text: str) -> int:
    """Estimate token count for text (rough approximation)."""
    return len(text) // CHARS_PER_TOKEN


def _create_agent(model_name: str | None = None) -> Agent[None, ScoringResponse]:
    """Create scoring agent with specified model (Gemini or Claude)."""
    if model_name is None:
        model_name = _get_default_model()

    if _is_anthropic_model(model_name):
        anthropic_settings = AnthropicModelSettings(
            temperature=0.2,
            timeout=300.0,
        )
        anthropic_model = AnthropicModel(model_name)
        return Agent(
            anthropic_model, output_type=ScoringResponse, model_settings=anthropic_settings
        )

    google_settings = GoogleModelSettings(
        temperature=0.2,
        google_thinking_config={"thinking_level": ThinkingLevel.LOW},
    )
    google_model = GoogleModel(model_name)
    return Agent(
        google_model, output_type=ScoringResponse, model_settings=google_settings
    )


def prepare_hotel_for_llm(
    hotel: dict[str, Any],
    currency: str = "EUR",
    min_price: float | None = None,
    max_price: float | None = None,
) -> dict[str, Any]:
    """Prepare hotel data for LLM scoring with key information."""
    rates_info: list[dict[str, Any]] = []
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

        if len(rates_info) >= MAX_RATES_PER_HOTEL:
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

    amenities = [
        a.get("name", "") if isinstance(a, dict) else str(a)
        for g in hotel.get("amenity_groups", [])
        for a in g.get("amenities", [])
    ]

    hr = hotel.get("reviews", {})
    raw_reviews = hr.get("reviews", []) if isinstance(hr, dict) else []
    reviews = [
        {
            "id": r.get("id"),
            "rating": r.get("rating"),
            "plus": (r.get("review_plus") or "")[:REVIEW_TEXT_MAX_LENGTH],
            "minus": (r.get("review_minus") or "")[:REVIEW_TEXT_MAX_LENGTH],
        }
        for r in raw_reviews[:MAX_REVIEWS_PER_HOTEL]
    ]

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
        "amenities": amenities[:MAX_AMENITIES_PER_HOTEL],
        "reviews": reviews,
    }


def _build_prompt(hotels_data: list[dict[str, Any]], user_preferences: str) -> str:
    """Build scoring prompt for a batch of hotels."""
    return SCORING_PROMPT.format(
        user_preferences=user_preferences,
        hotels_json=json.dumps(hotels_data, ensure_ascii=False),
    )


async def score_hotels(
    hotels: list[dict[str, Any]],
    user_preferences: str,
    params: ScoringParams | None = None,
) -> AsyncIterator[ScoringResult]:
    """Score hotels based on user preferences using LLM.

    Args:
        hotels: List of hotel data to score.
        user_preferences: User preferences for scoring.
        params: Optional scoring parameters (currency, min_price, max_price, etc.).

    Yields:
        ScoringResult events: start, batch_start, retry, progress, error, done.
    """
    params = params or {}
    currency = params.get("currency", "EUR")
    min_price = params.get("min_price")
    max_price = params.get("max_price")
    batch_size = params.get("batch_size", DEFAULT_BATCH_SIZE)
    model_name = params.get("model_name")
    retries = params.get("retries", DEFAULT_RETRIES)

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

    all_results: list[dict[str, Any]] = []
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
        for attempt in range(retries):
            try:
                response = await agent.run(prompt)
                result = response.output
                break
            except (ValidationError, ValueError) as e:
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
            except (httpx.HTTPError, UnexpectedModelBehavior, RuntimeError, OSError) as e:
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

        if result is not None:
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
