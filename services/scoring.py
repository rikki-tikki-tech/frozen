"""LLM-based hotel scoring using Google Gemini or Anthropic Claude."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

import httpx
from google.genai.types import ThinkingLevel
from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

from config import SCORING_MODEL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .hotels import HotelFull, HotelScored


class HotelScoreDict(TypedDict):
    """Individual hotel score as dict."""

    hotel_id: str
    score: int
    top_reasons: list[str]
    score_penalties: list[str]


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


ScoringEventType = Literal["start", "batch_start", "progress", "retry", "error", "done"]


class ScoringResult(TypedDict):
    """Union result type for scoring events.

    Contains one of: start, batch_start, progress, retry, error, or done events.
    """

    type: ScoringEventType
    start: ScoringStart | None
    batch_start: ScoringBatchStart | None
    progress: ScoringProgress | None
    retry: ScoringRetry | None
    error: ScoringError | None
    results: list[HotelScoreDict] | None


class ScoringParams(TypedDict, total=False):
    """Optional scoring parameters.

    All fields are optional with sensible defaults.
    """

    batch_size: int
    model_name: str
    retries: int


SCORING_PROMPT = """\
You are a hotel recommendation expert. Score hotels based on user preferences.

## User Preferences
{user_preferences}

## Hotels to Score
{hotels_json}

## Scoring Guidelines

**Score Range (0-100):**
- 90-100: Excellent match — meets all key preferences, no significant drawbacks
- 70-89: Good match — meets most preferences, minor compromises
- 50-69: Acceptable — meets some preferences, notable gaps
- 30-49: Poor match — significant misalignment with preferences
- 0-29: Very poor — fails to meet most preferences

**Critical Rule — Explicit Preference Violations:**
If user explicitly stated a preference and hotel does NOT meet it,
apply HEAVY penalty (-15 to -30 points per violation).
- User said "с бассейном" → hotel has no pool → severe penalty
- User said "в центре" → hotel is far from center → severe penalty

Missing features user did NOT mention = minor penalty (-5 to -10).
Violating features user DID mention = major penalty (-15 to -30).

**Evaluation Criteria (prioritize based on user preferences):**
1. Location relevance (proximity to stated interests, landmarks, transport)
2. Price alignment with budget expectations
3. Amenities matching stated needs (Wi-Fi, parking, pool, etc.)
4. Star rating / quality level fit
5. Guest reviews and reputation
6. Room type suitability

**Instructions:**
1. First, identify explicit user requirements from their preferences
2. Check each hotel against these explicit requirements — violations are critical
3. Then evaluate general fit
4. Assign a score reflecting overall fit (explicit violations hurt much more)
5. top_reasons: 1-5 concise phrases (≤10 words) why this hotel works for user
6. score_penalties: facts explaining deductions — mention explicit violations first
7. Return ALL hotels sorted by score descending
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


def prepare_hotel_for_llm(hotel: HotelFull) -> dict[str, Any]:
    """Prepare hotel data for LLM scoring with key information."""
    rates_info: list[dict[str, Any]] = []
    for rate in hotel.get("rates", []):
        if len(rates_info) >= MAX_RATES_PER_HOTEL:
            break

        pt = rate.get("payment_options", {}).get("payment_types", [])
        price_str = pt[0].get("show_amount") if pt else None
        currency = pt[0].get("show_currency_code", "") if pt else ""
        meal_data = rate.get("meal_data", {})

        rate_info = {
            "room": rate.get("room_name", "")[:60],
            "price": f"{price_str} {currency}" if price_str else None,
            "meal": meal_data.get("value", rate.get("meal", "")),
            "has_breakfast": meal_data.get("has_breakfast", False),
        }

        for p in pt:
            cp = p.get("cancellation_penalties", {})
            free_cancel = cp.get("free_cancellation_before")
            if free_cancel:
                rate_info["free_cancel_before"] = free_cancel[:10]
                break

        rates_info.append(rate_info)

    amenities = [
        a
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
    hotels: list[HotelFull],
    user_preferences: str,
    params: ScoringParams | None = None,
) -> AsyncIterator[ScoringResult]:
    """Score hotels based on user preferences using LLM.

    Args:
        hotels: List of combined hotel data to score.
        user_preferences: User preferences for scoring.
        params: Optional scoring parameters (batch_size, model_name, retries).

    Yields:
        ScoringResult events: start, batch_start, retry, progress, error, done.
    """
    params = params or {}
    batch_size = params.get("batch_size", DEFAULT_BATCH_SIZE)
    model_name = params.get("model_name")
    retries = params.get("retries", DEFAULT_RETRIES)

    agent = _create_agent(model_name)

    hotels_for_llm = [prepare_hotel_for_llm(h) for h in hotels]

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

    all_results: list[HotelScoreDict] = []
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
            all_results.extend([cast("HotelScoreDict", h.model_dump()) for h in result.results])

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


# =============================================================================
# Summarization
# =============================================================================

SUMMARY_TOP_HOTELS = 10


class HotelRecommendation(BaseModel):
    """Single hotel recommendation in summary."""

    hotel_id: str
    hotel_name: str
    why_recommended: str


class SearchSummary(BaseModel):
    """LLM-generated summary of search results."""

    overview: str
    top_picks: list[HotelRecommendation]
    considerations: str
    final_advice: str


SUMMARY_PROMPT = """\
You are a helpful travel assistant. User searched for hotels with preferences:

## User Preferences
{user_preferences}

## Top {top_count} Search Results (sorted by score)
{hotels_json}

## Your Task

Provide a helpful summary for the user:

1. **overview**: 2-3 sentences about how well the search matched preferences.
   Be honest — if results don't match well, say so.

2. **top_picks**: List 2-4 best hotels with:
   - hotel_id: exact ID from the data
   - hotel_name: hotel name
   - why_recommended: 1-2 sentences why THIS hotel fits THIS user's needs

3. **considerations**: Important trade-offs to consider
   (price vs location, missing amenities, etc.)

4. **final_advice**: One clear recommendation — which hotel to book and why,
   or what to search for differently if results are poor.

Be concise, specific, and helpful. Reference actual hotel names and features.
"""


def _create_summary_agent(model_name: str | None = None) -> Agent[None, SearchSummary]:
    """Create summarization agent with specified model."""
    if model_name is None:
        model_name = _get_default_model()

    if _is_anthropic_model(model_name):
        anthropic_settings = AnthropicModelSettings(
            temperature=0.3,
            timeout=120.0,
        )
        anthropic_model = AnthropicModel(model_name)
        return Agent(
            anthropic_model, output_type=SearchSummary, model_settings=anthropic_settings
        )

    google_settings = GoogleModelSettings(
        temperature=0.3,
    )
    google_model = GoogleModel(model_name)
    return Agent(
        google_model, output_type=SearchSummary, model_settings=google_settings
    )


def _prepare_hotel_for_summary(hotel: HotelScored) -> dict[str, Any]:
    """Prepare scored hotel data for summary prompt."""
    return {
        "hotel_id": hotel.get("id", ""),
        "name": hotel.get("name", ""),
        "score": hotel.get("score", 0),
        "top_reasons": hotel.get("top_reasons", []),
        "score_penalties": hotel.get("score_penalties", []),
        "stars": hotel.get("star_rating", 0),
        "kind": hotel.get("kind", ""),
        "address": hotel.get("address", ""),
    }


class SummaryResult(TypedDict):
    """Result of summarize_results function."""

    summary: SearchSummary | None
    error: str | None


async def summarize_results(
    scored_hotels: list[HotelScored],
    user_preferences: str,
    model_name: str | None = None,
    top_count: int = SUMMARY_TOP_HOTELS,
) -> SummaryResult:
    """Generate LLM summary of search results.

    Args:
        scored_hotels: List of scored hotels.
        user_preferences: Original user search preferences.
        model_name: Optional model name override.
        top_count: Number of top hotels to include in summary.

    Returns:
        SummaryResult with summary or error.
    """
    agent = _create_summary_agent(model_name)

    top_hotels = scored_hotels[:top_count]
    hotels_for_summary = [_prepare_hotel_for_summary(h) for h in top_hotels]

    prompt = SUMMARY_PROMPT.format(
        user_preferences=user_preferences,
        top_count=len(hotels_for_summary),
        hotels_json=json.dumps(hotels_for_summary, ensure_ascii=False, indent=2),
    )

    try:
        response = await agent.run(prompt)
    except (ValidationError, ValueError, httpx.HTTPError, UnexpectedModelBehavior) as e:
        return {"summary": None, "error": str(e)}
    else:
        return {"summary": response.output, "error": None}
