"""LLM-based hotel scoring using Google Gemini or Anthropic Claude."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypedDict

import httpx
from google.genai.types import ThinkingLevel
from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

from config import SCORING_MODEL

if TYPE_CHECKING:
    from .hotels import HotelFull


# =============================================================================
# Types
# =============================================================================


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
    """LLM response with top hotels and summary."""

    results: list[HotelScore]
    summary: str


class ScoringResultDict(TypedDict):
    """Result of score_hotels function."""

    results: list[HotelScoreDict]
    summary: str
    error: str | None
    estimated_tokens: int


# =============================================================================
# Configuration
# =============================================================================

SCORING_PROMPT = """\
You are a hotel recommendation expert. Analyze all hotels and select TOP 10.

## User Preferences
{user_preferences}

## Hotels to Analyze ({total_hotels} total)
{hotels_json}

## Your Task

1. **Extract criteria** — Identify explicit user requirements from their preferences
2. **Evaluate completely** — Score ALL available hotels against the criteria (0-100 scale)
3. **Rank and select** — Sort by score descending, return TOP 10 with full details
4. **Summarize** — Generate a text summary explaining why these hotels were selected

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

**Evaluation Criteria (prioritize based on user preeferences):**
1. Location relevance (proximity to stated interests, landmarks, transport)
2. Price alignment with budget expectations
3. Amenities matching stated needs (Wi-Fi, parking, pool, etc.)
4. Star rating / quality level fit
5. Guest reviews and reputation
6. Room type suitability

**For each hotel in TOP 10:**
- top_reasons: 1-5 phrases why this hotel matches user needs
- score_penalties: explain several phrases 1-5: what's missing, what doesn't match preferences (empty if none)

**Summary (text format):**
Write a brief comparison explaining why selected hotels are better than alternatives.
Include specific examples with hotel IDs, names, and prices to illustrate:
- Price range of all evaluated hotels (e.g., "от 300 до 2000")
- Why cheaper hotels scored lower (e.g., "Hotel X (id: abc, 320₽) — нет бассейна, далеко от центра")
- Why some expensive hotels didn't make top 10 (if applicable)
- Key tradeoffs user should know about (e.g., "все отели до 500₽ без завтрака")

Goal: user should understand the full picture without manually reviewing rejected options.
"""

TOP_HOTELS_COUNT = 10
DEFAULT_RETRIES = 3
CHARS_PER_TOKEN = 3

MAX_RATES_PER_HOTEL = 3
MAX_REVIEWS_PER_HOTEL = 5
MAX_AMENITIES_PER_HOTEL = 15
REVIEW_TEXT_MAX_LENGTH = 100


# =============================================================================
# Helpers
# =============================================================================


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
        anthropic_settings = AnthropicModelSettings(temperature=0.2, timeout=300.0)
        anthropic_model = AnthropicModel(model_name)
        return Agent(
            anthropic_model, output_type=ScoringResponse, model_settings=anthropic_settings
        )

    google_settings = GoogleModelSettings(
        temperature=0.2,
        google_thinking_config={"thinking_level": ThinkingLevel.LOW},
    )
    google_model = GoogleModel(model_name)
    return Agent(google_model, output_type=ScoringResponse, model_settings=google_settings)


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
    """Build scoring prompt for hotels."""
    return SCORING_PROMPT.format(
        user_preferences=user_preferences,
        total_hotels=len(hotels_data),
        hotels_json=json.dumps(hotels_data, ensure_ascii=False),
    )


# =============================================================================
# Main Function
# =============================================================================


async def score_hotels(
    hotels: list[HotelFull],
    user_preferences: str,
    model_name: str | None = None,
    retries: int = DEFAULT_RETRIES,
) -> ScoringResultDict:
    """Score hotels and return top 10 with summary.

    Single LLM call that analyzes all hotels and returns:
    - Top 10 scored hotels
    - Summary explaining price range, trade-offs, why cheaper options are worse

    Args:
        hotels: List of combined hotel data to score.
        user_preferences: User preferences for scoring.
        model_name: Optional model name override.
        retries: Number of retry attempts on failure.

    Returns:
        ScoringResultDict with results, summary, error, and token estimate.
    """
    agent = _create_agent(model_name)

    hotels_for_llm = [prepare_hotel_for_llm(h) for h in hotels]
    prompt = _build_prompt(hotels_for_llm, user_preferences)
    estimated_tokens = estimate_tokens(prompt)

    last_error: str | None = None

    for _attempt in range(retries):
        try:
            response = await agent.run(prompt)
        except (ValidationError, ValueError) as e:
            last_error = f"Validation error: {e}"
            continue
        except (httpx.HTTPError, UnexpectedModelBehavior, RuntimeError, OSError) as e:
            last_error = f"{type(e).__name__}: {e}"
            break
        else:
            results = [
                HotelScoreDict(
                    hotel_id=h.hotel_id,
                    score=h.score,
                    top_reasons=h.top_reasons,
                    score_penalties=h.score_penalties,
                )
                for h in response.output.results[:TOP_HOTELS_COUNT]
            ]
            return {
                "results": results,
                "summary": response.output.summary,
                "error": None,
                "estimated_tokens": estimated_tokens,
            }

    return {
        "results": [],
        "summary": "",
        "error": last_error,
        "estimated_tokens": estimated_tokens,
    }
