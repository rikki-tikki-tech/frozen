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
    from etg import GuestRoom

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
You are an expert Hotel Recommendation Engine specializing in "Value for Money" analysis.

## Input Data
1. **Guests:** {guests_info}
2. **Price Range:** {price_range}
3. **User Preferences:** {user_preferences}
4. **Hotels List:** {hotels_json}

## Core Scoring Philosophy (The "Brain")
**CRITICAL:** Do NOT simply rank by lowest price.
1. **Value for Money:** A $200 5-star hotel is often better than a $100 3-star hotel. Award higher scores to high-quality properties if the price is reasonable for that class.
2. **Star Rating Weight:** Treat Star Rating as a proxy for service quality. High stars should boost the score unless the user specifically asked for "budget only".
3. **Preference Matching:**
   - **Boost:** Heavy points for explicit amenities (e.g., User: "pool" -> Hotel: "has pool").
   - **Penalty:** Heavy deductions for missing explicit needs (e.g., User: "gym" -> Hotel: "no gym").

## Field Content Guidelines

Generate the response based on the provided schema. Follow these specific instructions for the content of each field:

### 1. `score` (integer 0-100)
- **90-100 (Excellent):** Perfect match OR incredible luxury for a good price.
- **70-89 (Good):** Meets needs, fair price, good stars.
- **50-69 (Average):** Cheap but low quality, or expensive but average quality.
- **0-49 (Poor):** Missing critical user requirements or extremely overpriced.

### 2. `top_reasons` (list of strings)
Do not write generic fluff. Be specific about value.
- **Format:** [Specific Match], [Value Proposition], [Quality Indicator].
- **Examples:**
  - "Includes requested swimming pool"
  - "Excellent 5-star rating for only $150 (Great Deal)"
  - "Located in City Center as requested"

### 3. `score_penalties` (list of strings)
Explain exactly why the score is not 100.
- **Format:** [Missing Feature], [Quality Issue], [Price Issue].
- **Examples:**
  - "Missing requested breakfast"
  - "Far from city center (5km)"
  - "Low guest rating (6.5/10)"
  - "Price ($400) is too high for a 3-star hotel"

### 4. `summary` (string)
Write a strategic market analysis (4-6 sentences) helping the user understand the choice.
**CRITICAL REQUIREMENT:** You MUST cite specific examples of lower-ranked hotels to explain the trade-offs.
- **Range:** Start with the price range analyzed.
- **Contrast:** Explain why the Top 10 are better than specific cheaper/rejected alternatives.
- **Explicit Citations:** When mentioning rejected hotels, you **MUST include their Name and ID**.
- **Example Pattern:** "We analyzed hotels from $50 to $500. While 'Budget Inn' (ID: 123) is the cheapest ($50), it was ranked low due to missing AC. 'Luxury Stay' (ID: 456) was excluded because its $500 price tag is not justified by its 3-star rating. The selected winners offer the best balance of price and amenities."
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
        google_thinking_config={"thinking_level": ThinkingLevel.MEDIUM},
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



def _format_guests_info(guests: list[GuestRoom]) -> str:
    """Format guests info for prompt."""
    total_adults = sum(g.get("adults", 0) for g in guests)
    all_children = [age for g in guests for age in g.get("children", [])]

    if all_children:
        ages = ", ".join(map(str, all_children))
        return f"{total_adults} adults, {len(all_children)} children (ages: {ages})"
    return f"{total_adults} adults"


def _format_price_range(
    min_price: float | None,
    max_price: float | None,
    currency: str | None,
) -> str:
    """Format price range for prompt."""
    curr = currency or "RUB"
    if min_price is not None and max_price is not None:
        return f"{min_price:.0f} - {max_price:.0f} {curr} per night"
    if min_price is not None:
        return f"from {min_price:.0f} {curr} per night"
    if max_price is not None:
        return f"up to {max_price:.0f} {curr} per night"
    return "not specified"


def _build_prompt(  # noqa: PLR0913
    hotels_data: list[dict[str, Any]],
    user_preferences: str,
    guests: list[GuestRoom],
    min_price: float | None,
    max_price: float | None,
    currency: str | None,
) -> str:
    """Build scoring prompt for hotels."""
    return SCORING_PROMPT.format(
        guests_info=_format_guests_info(guests),
        price_range=_format_price_range(min_price, max_price, currency),
        user_preferences=user_preferences,
        total_hotels=len(hotels_data),
        hotels_json=json.dumps(hotels_data, ensure_ascii=False),
    )


# =============================================================================
# Main Function
# =============================================================================


async def score_hotels(  # noqa: PLR0913
    hotels: list[HotelFull],
    user_preferences: str,
    guests: list[GuestRoom],
    min_price: float | None = None,
    max_price: float | None = None,
    currency: str | None = None,
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
        guests: List of room configurations with adults and children.
        min_price: Minimum price per night filter (or None if not set).
        max_price: Maximum price per night filter (or None if not set).
        currency: Currency code (e.g., 'RUB', 'USD').
        model_name: Optional model name override.
        retries: Number of retry attempts on failure.

    Returns:
        ScoringResultDict with results, summary, error, and token estimate.
    """
    agent = _create_agent(model_name)

    hotels_for_llm = [prepare_hotel_for_llm(h) for h in hotels]
    prompt = _build_prompt(
        hotels_for_llm, user_preferences, guests, min_price, max_price, currency
    )
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
