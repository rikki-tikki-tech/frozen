"""LLM-based hotel scoring using Google Gemini or Anthropic Claude."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import httpx
from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from config import SCORING_MODEL
from services.llm_providers import create_agent, estimate_tokens

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
    selected_rate_hash: str | None


class HotelScore(BaseModel):
    """Individual hotel score from LLM evaluation."""

    hotel_id: str
    score: int
    top_reasons: list[str]
    score_penalties: list[str]
    selected_rate_hash: str | None


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

def _load_scoring_prompt() -> str:
    """Load scoring prompt from markdown file."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "hotel_scoring.md"
    return prompt_path.read_text(encoding="utf-8")

SCORING_PROMPT_TEMPLATE = _load_scoring_prompt()

TOP_HOTELS_COUNT = 10
DEFAULT_RETRIES = 3

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


def _create_agent(model_name: str | None = None) -> Agent[None, ScoringResponse]:
    """Create scoring agent with specified model."""
    return create_agent(model_name or _get_default_model(), ScoringResponse)


def prepare_hotel_for_llm(hotel: HotelFull) -> dict[str, Any]:
    """Prepare hotel data for LLM scoring with key information."""
    rates_info: list[dict[str, Any]] = []
    for rate in hotel.get("rates", []):
        if len(rates_info) >= MAX_RATES_PER_HOTEL:
            break

        payment_types = rate.get("payment_options", {}).get("payment_types", [])
        meal_data = rate.get("meal_data", {})

        price_str = payment_types[0].get("show_amount") if payment_types else None
        currency = payment_types[0].get("show_currency_code", "") if payment_types else ""

        rate_info = {
            "match_hash": rate.get("match_hash", ""),
            "room": rate.get("room_name", "")[:60],
            "price": f"{price_str} {currency}" if price_str else None,
            "meal": meal_data.get("value", rate.get("meal", "")),
            "has_breakfast": meal_data.get("has_breakfast", False),
            "daily_prices": rate.get("daily_prices", []),
            "room_name_info": rate.get("room_name_info"),
            "room_data_trans": rate.get("room_data_trans", {}),
            "rg_ext": rate.get("rg_ext", {}),
            "amenities_data": rate.get("amenities_data", []),
            "serp_filters": rate.get("serp_filters", []),
            "deposit": rate.get("deposit"),
            "no_show": rate.get("no_show"),
            "legal_info": rate.get("legal_info"),
            "allotment": rate.get("allotment"),
            "any_residency": rate.get("any_residency"),
            "is_package": rate.get("is_package"),
            "payment_options": [],
        }

        for payment_type in payment_types:
            cancellation_penalties = payment_type.get("cancellation_penalties", {})
            free_cancel = cancellation_penalties.get("free_cancellation_before")
            if free_cancel and not rate_info.get("free_cancel_before"):
                rate_info["free_cancel_before"] = free_cancel[:10]
            rate_info["payment_options"].append(
                {
                    "type": payment_type.get("type"),
                    "show_amount": payment_type.get("show_amount"),
                    "show_currency_code": payment_type.get("show_currency_code"),
                    "by": payment_type.get("by"),
                    "is_need_credit_card_data": payment_type.get("is_need_credit_card_data"),
                    "is_need_cvc": payment_type.get("is_need_cvc"),
                    "tax_data": payment_type.get("tax_data"),
                    "cancellation_penalties": cancellation_penalties,
                }
            )

        rates_info.append(rate_info)

    amenities = [
        amenity
        for group in hotel.get("amenity_groups", [])
        for amenity in group.get("amenities", [])
    ]

    hotel_reviews = hotel.get("reviews", {})
    raw_reviews = hotel_reviews.get("reviews", []) if isinstance(hotel_reviews, dict) else []
    reviews_sample = [
        {
            "id": review.get("id"),
            "rating": review.get("rating"),
            "plus": (review.get("review_plus") or "")[:REVIEW_TEXT_MAX_LENGTH],
            "minus": (review.get("review_minus") or "")[:REVIEW_TEXT_MAX_LENGTH],
            "created": review.get("created"),
            "traveller_type": review.get("traveller_type"),
            "trip_type": review.get("trip_type"),
            "nights": review.get("nights"),
            "room_name": review.get("room_name"),
            "language": review.get("_lang"),
        }
        for review in raw_reviews[:MAX_REVIEWS_PER_HOTEL]
    ]

    # Add aggregated review statistics
    reviews_data = {
        "total_reviews": (
            hotel_reviews.get("total_reviews", 0) if isinstance(hotel_reviews, dict) else 0
        ),
        "avg_rating": (
            hotel_reviews.get("avg_rating") if isinstance(hotel_reviews, dict) else None
        ),
        "detailed_averages": (
            hotel_reviews.get("detailed_averages", {}) if isinstance(hotel_reviews, dict) else {}
        ),
        "sample_reviews": reviews_sample,
    }

    room_groups = hotel.get("room_groups", [])
    room_groups_info = [
        {
            "room_group_id": group.get("room_group_id"),
            "name": group.get("name"),
            "name_struct": group.get("name_struct"),
            "room_amenities": group.get("room_amenities"),
            "rg_ext": group.get("rg_ext"),
            "images": group.get("images"),
            "images_ext": group.get("images_ext"),
        }
        for group in room_groups
    ]

    amenity_groups = [
        {
            "group_name": group.get("group_name"),
            "amenities": group.get("amenities", []),
            "non_free_amenities": group.get("non_free_amenities"),
        }
        for group in hotel.get("amenity_groups", [])
    ]

    images_ext = hotel.get("images_ext", [])
    images_summary = {
        "total": len(images_ext),
        "categories": sorted({image.get("category_slug") for image in images_ext if image}),
    }

    return {
        "hotel_id": hotel.get("id", ""),
        "name": hotel.get("name", ""),
        "stars": hotel.get("star_rating", 0),
        "kind": hotel.get("kind", ""),
        "address": hotel.get("address", ""),
        "latitude": hotel.get("latitude"),
        "longitude": hotel.get("longitude"),
        "region": hotel.get("region", {}),
        "postal_code": hotel.get("postal_code"),
        "hotel_chain": hotel.get("hotel_chain"),
        "is_closed": hotel.get("is_closed"),
        "deleted": hotel.get("deleted"),
        "check_in_time": hotel.get("check_in_time"),
        "check_out_time": hotel.get("check_out_time"),
        "front_desk_time_start": hotel.get("front_desk_time_start"),
        "front_desk_time_end": hotel.get("front_desk_time_end"),
        "description": hotel.get("description_struct", ""),
        "policy": hotel.get("policy_struct", []),
        "metapolicy": hotel.get("metapolicy_struct", {}),
        "metapolicy_extra_info": hotel.get("metapolicy_extra_info"),
        "facts": hotel.get("facts", []),
        "star_certificate": hotel.get("star_certificate", {}),
        "keys_pickup": hotel.get("keys_pickup", {}),
        "payment_methods": hotel.get("payment_methods", []),
        "serp_filters": hotel.get("serp_filters", []),
        "rates": rates_info,
        "amenities": amenities[:MAX_AMENITIES_PER_HOTEL],
        "amenity_groups": amenity_groups,
        "room_groups": room_groups_info,
        "images": images_summary,
        "reviews": reviews_data,
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
    min_price: float | None = None,
    max_price: float | None = None,
    currency: str | None = None,
    model_name: str | None = None,
    retries: int = DEFAULT_RETRIES,
    top_count: int = TOP_HOTELS_COUNT,
) -> ScoringResultDict:
    """Score hotels and return top N with summary.

    Single LLM call that analyzes all hotels and returns:
    - Top N scored hotels (configurable via top_count)
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
        top_count: Number of top hotels to return from LLM.

    Returns:
        ScoringResultDict with results, summary, error, and token estimate.
    """
    # Resolve model name for tokenizer and agent
    resolved_model = model_name or _get_default_model()
    agent = _create_agent(resolved_model)
    top_count = min(top_count, len(hotels))

    hotels_for_llm = [prepare_hotel_for_llm(h) for h in hotels]
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
