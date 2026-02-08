"""LLM-based hotel scoring using Google Gemini or Anthropic Claude."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import httpx
from pydantic import BaseModel, ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior

from config import SCORING_MODEL
from services.llm_providers import create_agent, estimate_tokens

from .hotels import filter_rates_by_price

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from etg import GuestRoom, HotelRate

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
    selected_rate_hash: str | None


class HotelScore(BaseModel):
    """Individual hotel score from LLM evaluation."""

    hotel_id: str
    score: int
    top_reasons: list[str]
    score_penalties: list[str]
    selected_rate_hash: str | None


class ScoringResponse(BaseModel):
    """LLM response with top scored hotels."""

    results: list[HotelScore]


class ScoringResultDict(TypedDict):
    """Result of score_hotels function."""

    results: list[HotelScoreDict]
    error: str | None
    estimated_tokens: int


# =============================================================================
# Configuration
# =============================================================================

def _load_scoring_prompt() -> str:
    """Load scoring prompt from markdown file."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "hotel_scoring.md"
    return prompt_path.read_text(encoding="utf-8")

SCORING_PROMPT_TEMPLATE = _load_scoring_prompt()

TOP_HOTELS_COUNT = 10
DEFAULT_RETRIES = 3


# =============================================================================
# Helpers
# =============================================================================


def _get_default_model() -> str:
    """Get the default scoring model from configuration."""
    return SCORING_MODEL


def _create_agent(model_name: str | None = None) -> Agent[None, ScoringResponse]:
    """Create scoring agent with specified model."""
    return create_agent(model_name or _get_default_model(), ScoringResponse)


def _build_rate(rate: HotelRate) -> dict[str, Any]:
    rate_info: dict[str, Any] = {
        "match_hash": rate.get("match_hash", ""),
        "daily_prices": rate.get("daily_prices", []),
        "meal_data": rate.get("meal_data", {}),
        "room_name": (rate.get("room_name") or "")[:200],
        "amenities_data": rate.get("amenities_data", []),
        "deposit": rate.get("deposit"),
    }

    return rate_info


def _build_facts(facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "year_built": facts.get("year_built"),
        "year_renovated": facts.get("year_renovated")
    }


def _review_date_key(review: dict[str, Any]) -> str:
    created = review.get("created")
    if isinstance(created, str):
        return created[:10]
    return ""


def _build_review_sample(
    raw_reviews: list[dict[str, Any]],
    max_reviews: int,
    review_text_max_length: int,
) -> list[dict[str, Any]]:
    # Filter reviews that have non-empty plus or minus text
    filtered: list[dict[str, Any]] = []

    for r in raw_reviews:
        plus = (r.get("review_plus") or "").strip()
        minus = (r.get("review_minus") or "").strip()
        if plus or minus:
            filtered.append(r)

    # Sort by date, newest first
    filtered.sort(key=_review_date_key, reverse=True)

    # Take top max_reviews reviews
    return [
        {
            "rating": r.get("rating"),
            "created": (r.get("created") or "")[:10],
            "plus": (r.get("review_plus") or "")[:review_text_max_length],
            "minus": (r.get("review_minus") or "")[:review_text_max_length],
        }
        for r in filtered[:max_reviews]
    ]


def prepare_hotel_for_llm(
    hotel: HotelFull,
    min_price: float | None,
    max_price: float | None,
    max_reviews: int,
    review_text_max_length: int,
) -> dict[str, Any]:
    """Prepare hotel data for LLM scoring.

    Args:
        hotel: Combined hotel data.
        min_price: Minimum price per night filter (or None).
        max_price: Maximum price per night filter (or None).
        max_reviews: Maximum number of reviews to include.
        review_text_max_length: Maximum length of review text.

    Returns:
        Hotel data formatted for LLM.
    """
    raw_rates = hotel.get("rates", []) or []
    filtered_rates = filter_rates_by_price(raw_rates, min_price, max_price)
    rates = [_build_rate(rate) for rate in filtered_rates]

    facts_dict: dict[str, Any] = hotel.get("facts") or {}  # type: ignore[assignment]
    facts_summary = _build_facts(facts_dict)

    hr = hotel.get("reviews", {})
    raw_reviews = hr.get("reviews", []) if isinstance(hr, dict) else []
    reviews_sample = _build_review_sample(raw_reviews, max_reviews, review_text_max_length)

    reviews_data = {
        "total_reviews": hr.get("total_reviews", 0) if isinstance(hr, dict) else 0,
        "avg_rating": hr.get("avg_rating") if isinstance(hr, dict) else None,
        "detailed_averages": hr.get("detailed_averages", {}) if isinstance(hr, dict) else {},
        "reviews": reviews_sample,
    }

    return {
        "hotel_id": hotel.get("id", ""),
        "hid": hotel.get("hid"),
        "name": hotel.get("name", ""),
        "stars": hotel.get("star_rating", 0),
        "kind": hotel.get("kind", ""),
        "hotel_chain": hotel.get("hotel_chain"),
        "address": hotel.get("address", ""),
        "check_in_time": hotel.get("check_in_time"),
        "check_out_time": hotel.get("check_out_time"),
        "metapolicy_struct": hotel.get("metapolicy_struct", {}) or {},
        "facts_summary": facts_summary,
        "rates": rates,
        "reviews": reviews_data,
        "serp_filters": hotel.get("serp_filters"),
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
    top_count: int,
) -> str:
    """Build scoring prompt for hotels."""
    return SCORING_PROMPT_TEMPLATE.format(
        guests_info=_format_guests_info(guests),
        price_range=_format_price_range(min_price, max_price, currency),
        user_preferences=user_preferences,
        total_hotels=len(hotels_data),
        hotels_json=json.dumps(hotels_data, ensure_ascii=False),
        top_count=top_count,
    )


# =============================================================================
# Main Function
# =============================================================================


async def score_hotels(  # noqa: PLR0913
    hotels: list[HotelFull],
    user_preferences: str,
    guests: list[GuestRoom],
    max_reviews: int,
    review_text_max_length: int,
    min_price: float | None = None,
    max_price: float | None = None,
    currency: str | None = None,
    model_name: str | None = None,
    retries: int = DEFAULT_RETRIES,
    top_count: int = TOP_HOTELS_COUNT,
) -> ScoringResultDict:
    """Score hotels and return top N.

    Single LLM call that analyzes all hotels and returns
    top N scored hotels (configurable via top_count).

    Args:
        hotels: List of combined hotel data to score.
        user_preferences: User preferences for scoring.
        guests: List of room configurations with adults and children.
        max_reviews: Maximum number of reviews to include per hotel.
        review_text_max_length: Maximum length of review text.
        min_price: Minimum price per night filter (or None if not set).
        max_price: Maximum price per night filter (or None if not set).
        currency: Currency code (e.g., 'RUB', 'USD').
        model_name: Optional model name override.
        retries: Number of retry attempts on failure.
        top_count: Number of top hotels to return from LLM.

    Returns:
        ScoringResultDict with results, error, and token estimate.
    """
    # Resolve model name for tokenizer and agent
    resolved_model = model_name or _get_default_model()
    agent = _create_agent(resolved_model)
    top_count = min(top_count, len(hotels))

    hotels_for_llm = [
        prepare_hotel_for_llm(h, min_price, max_price, max_reviews, review_text_max_length)
        for h in hotels
    ]
    prompt = _build_prompt(
        hotels_for_llm, user_preferences, guests, min_price, max_price, currency, top_count
    )
    estimated_tokens = estimate_tokens(prompt, resolved_model)

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
                    selected_rate_hash=h.selected_rate_hash,
                )
                for h in response.output.results[:top_count]
            ]
            return {
                "results": results,
                "error": None,
                "estimated_tokens": estimated_tokens,
            }

    return {
        "results": [],
        "error": last_error,
        "estimated_tokens": estimated_tokens,
    }
