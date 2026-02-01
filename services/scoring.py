"""LLM-based hotel scoring using Google Gemini or Anthropic Claude."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

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

TOP_HOTELS_COUNT = 10
DEFAULT_RETRIES = 3

MAX_RATES_PER_HOTEL = 12
MAX_REVIEWS_PER_HOTEL = 20
MAX_AMENITIES_PER_HOTEL = 60
REVIEW_TEXT_MAX_LENGTH = 200
MAX_DESCRIPTION_PARAGRAPH_LENGTH = 400
MAX_POLICY_PARAGRAPH_LENGTH = 400


# =============================================================================
# Helpers
# =============================================================================


def _get_default_model() -> str:
    """Get the default scoring model from configuration."""
    return SCORING_MODEL


def _is_anthropic_model(model_name: str) -> bool:
    return model_name.startswith("claude-")


def estimate_tokens(text: str, model_name: str | None = None) -> int:
    """Estimate token count for text.

    Uses character-based estimation calibrated per model family:
    - Gemini: ~4 characters per token (Google documentation)
    - Claude: ~3.5 characters per token (Anthropic documentation)

    Args:
        text: Text to count tokens for.
        model_name: Optional model name for calibrated estimation.

    Returns:
        Estimated token count.
    """
    if model_name is None:
        model_name = _get_default_model()

    # Calibrated estimation per model family
    if _is_anthropic_model(model_name):
        # Claude: ~3.5 chars per token
        return int(len(text) / 3.5)

    # Gemini: ~4 chars per token (per Google docs)
    return len(text) // 4


def _create_agent(model_name: str | None = None) -> Agent[None, ScoringResponse]:
    """Create scoring agent with specified model (Gemini or Claude)."""
    if model_name is None:
        model_name = _get_default_model()

    if _is_anthropic_model(model_name):
        anthropic_settings = AnthropicModelSettings(temperature=0.2, timeout=300.0)
        anthropic_model = AnthropicModel(model_name)
        agent = Agent(
            anthropic_model, output_type=ScoringResponse, model_settings=anthropic_settings
        )
        return cast("Agent[None, ScoringResponse]", cast("object", agent))

    google_settings = GoogleModelSettings(
        temperature=0.2,
        google_thinking_config={"thinking_level": ThinkingLevel.MEDIUM},
    )
    google_model = GoogleModel(model_name)
    agent = Agent(google_model, output_type=ScoringResponse, model_settings=google_settings)
    return cast("Agent[None, ScoringResponse]", cast("object", agent))


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_daily_prices(daily_prices: list[Any]) -> list[float]:
    prices: list[float] = []
    for p in daily_prices:
        price = _to_float(p)
        if price is not None and price > 0:
            prices.append(price)
    return prices


def _trim_paragraphs(paragraphs: list[str], max_len: int) -> list[str]:
    trimmed: list[str] = []
    for p in paragraphs:
        if not isinstance(p, str):
            continue
        if len(p) > max_len:
            trimmed.append(p[:max_len].rstrip())
        else:
            trimmed.append(p)
    return trimmed


def _summarize_images(images_ext: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for img in images_ext:
        cat = img.get("category_slug")
        if not isinstance(cat, str) or not cat:
            continue
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def _flatten_amenities(amenity_groups: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    flat: list[str] = []
    for group in amenity_groups:
        for name in group.get("amenities", []) or []:
            if isinstance(name, str) and name and name not in seen:
                seen.add(name)
                flat.append(name)
        for name in group.get("non_free_amenities", []) or []:
            if isinstance(name, str) and name and name not in seen:
                seen.add(name)
                flat.append(name)
    return flat


def _build_rate_info(rate: dict[str, Any]) -> dict[str, Any]:
    payment_types = rate.get("payment_options", {}).get("payment_types", []) or []
    payment = payment_types[0] if payment_types else {}
    meal_data = rate.get("meal_data", {}) or {}
    rg_ext = rate.get("rg_ext", {}) or {}

    daily_prices = _parse_daily_prices(rate.get("daily_prices", []) or [])
    nights = len(daily_prices) if daily_prices else None
    total_from_daily = sum(daily_prices) if daily_prices else None
    total_from_payment = _to_float(payment.get("show_amount"))
    total_price = total_from_daily if total_from_daily else total_from_payment

    currency = payment.get("show_currency_code") or payment.get("currency_code") or ""
    avg_price_per_night = (
        (total_price / nights) if (total_price is not None and nights) else None
    )

    cancellation = payment.get("cancellation_penalties", {}) or {}
    free_cancel_before = cancellation.get("free_cancellation_before")

    taxes = (payment.get("tax_data", {}) or {}).get("taxes", []) or []

    rate_info: dict[str, Any] = {
        "match_hash": rate.get("match_hash", ""),
        "search_hash": rate.get("search_hash"),
        "room": (rate.get("room_name") or "")[:120],
        "room_info": (rate.get("room_name_info") or "")[:200],
        "room_data_trans": rate.get("room_data_trans", {}),
        "rg_ext": rg_ext,
        "capacity": rg_ext.get("capacity"),
        "bedrooms": rg_ext.get("bedrooms"),
        "meal": meal_data.get("value", rate.get("meal", "")),
        "has_breakfast": meal_data.get("has_breakfast", False),
        "no_child_meal": meal_data.get("no_child_meal"),
        "nights": nights,
        "total_price": total_price,
        "avg_price_per_night": avg_price_per_night,
        "currency": currency,
        "price": f"{total_price:.0f} {currency}" if total_price and currency else None,
        "payment": {
            "type": payment.get("type"),
            "by": payment.get("by"),
            "amount": payment.get("amount"),
            "show_amount": payment.get("show_amount"),
            "currency_code": payment.get("currency_code"),
            "show_currency_code": payment.get("show_currency_code"),
            "is_need_credit_card_data": payment.get("is_need_credit_card_data"),
            "is_need_cvc": payment.get("is_need_cvc"),
        },
        "cancellation": {
            "free_cancel_before": free_cancel_before,
            "policies": cancellation.get("policies", []) or [],
        },
        "has_free_cancel": bool(free_cancel_before),
        "taxes": taxes,
        "amenities_data": rate.get("amenities_data", []) or [],
        "serp_filters": rate.get("serp_filters", []) or [],
        "any_residency": rate.get("any_residency"),
        "allotment": rate.get("allotment"),
        "deposit": rate.get("deposit"),
        "is_package": rate.get("is_package"),
        "legal_info": rate.get("legal_info"),
        "no_show": rate.get("no_show"),
    }

    return rate_info


def _summarize_rates(rates: list[dict[str, Any]]) -> dict[str, Any]:
    if not rates:
        return {
            "count": 0,
            "min_total_price": None,
            "max_total_price": None,
            "avg_total_price": None,
            "min_avg_price_per_night": None,
            "max_avg_price_per_night": None,
            "has_breakfast_count": 0,
            "free_cancel_count": 0,
            "max_capacity": None,
            "max_bedrooms": None,
            "meal_types": {},
            "currency": None,
        }

    totals = [r.get("total_price") for r in rates if isinstance(r.get("total_price"), (int, float))]
    avg_nights = [
        r.get("avg_price_per_night")
        for r in rates
        if isinstance(r.get("avg_price_per_night"), (int, float))
    ]
    capacities = [r.get("capacity") for r in rates if isinstance(r.get("capacity"), (int, float))]
    bedrooms = [r.get("bedrooms") for r in rates if isinstance(r.get("bedrooms"), (int, float))]

    meal_types: dict[str, int] = {}
    for r in rates:
        meal = r.get("meal")
        if isinstance(meal, str) and meal:
            meal_types[meal] = meal_types.get(meal, 0) + 1

    currency = None
    for r in rates:
        curr = r.get("currency")
        if isinstance(curr, str) and curr:
            currency = curr
            break

    return {
        "count": len(rates),
        "min_total_price": min(totals) if totals else None,
        "max_total_price": max(totals) if totals else None,
        "avg_total_price": (sum(totals) / len(totals)) if totals else None,
        "min_avg_price_per_night": min(avg_nights) if avg_nights else None,
        "max_avg_price_per_night": max(avg_nights) if avg_nights else None,
        "has_breakfast_count": sum(1 for r in rates if r.get("has_breakfast")),
        "free_cancel_count": sum(1 for r in rates if r.get("has_free_cancel")),
        "max_capacity": max(capacities) if capacities else None,
        "max_bedrooms": max(bedrooms) if bedrooms else None,
        "meal_types": meal_types,
        "currency": currency,
    }


def _select_rates(rates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(rates) <= limit:
        return rates

    def price_key(r: dict[str, Any]) -> float:
        return r.get("total_price") if isinstance(r.get("total_price"), (int, float)) else 1e18

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_rate(r: dict[str, Any]) -> None:
        match_hash = r.get("match_hash")
        if not isinstance(match_hash, str) or not match_hash:
            return
        if match_hash in seen:
            return
        seen.add(match_hash)
        selected.append(r)

    # Cheapest rates
    for r in sorted(rates, key=price_key)[:4]:
        add_rate(r)

    # Cheapest with breakfast
    breakfast = [r for r in rates if r.get("has_breakfast")]
    for r in sorted(breakfast, key=price_key)[:2]:
        add_rate(r)

    # Cheapest with free cancel
    free_cancel = [r for r in rates if r.get("has_free_cancel")]
    for r in sorted(free_cancel, key=price_key)[:2]:
        add_rate(r)

    # Highest capacity / bedrooms
    for r in sorted(rates, key=lambda x: (x.get("capacity") or 0), reverse=True)[:2]:
        add_rate(r)
    for r in sorted(rates, key=lambda x: (x.get("bedrooms") or 0), reverse=True)[:2]:
        add_rate(r)

    if len(selected) < limit:
        for r in sorted(rates, key=price_key):
            add_rate(r)
            if len(selected) >= limit:
                break

    return selected[:limit]


def _build_review_sample(raw_reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sample: list[dict[str, Any]] = []
    for r in raw_reviews[:MAX_REVIEWS_PER_HOTEL]:
        sample.append(
            {
                "id": r.get("id"),
                "rating": r.get("rating"),
                "created": (r.get("created") or "")[:10],
                "traveller_type": r.get("traveller_type"),
                "trip_type": r.get("trip_type"),
                "adults": r.get("adults"),
                "children": r.get("children"),
                "nights": r.get("nights"),
                "room_name": (r.get("room_name") or "")[:120],
                "plus": (r.get("review_plus") or "")[:REVIEW_TEXT_MAX_LENGTH],
                "minus": (r.get("review_minus") or "")[:REVIEW_TEXT_MAX_LENGTH],
                "detailed_review": r.get("detailed_review", {}) or {},
                "lang": r.get("_lang"),
            }
        )
    return sample


def _summarize_review_meta(raw_reviews: list[dict[str, Any]]) -> dict[str, Any]:
    traveller_types: dict[str, int] = {}
    trip_types: dict[str, int] = {}
    langs: dict[str, int] = {}
    latest_date = None

    for r in raw_reviews:
        ttype = r.get("traveller_type")
        if isinstance(ttype, str) and ttype:
            traveller_types[ttype] = traveller_types.get(ttype, 0) + 1
        trip = r.get("trip_type")
        if isinstance(trip, str) and trip:
            trip_types[trip] = trip_types.get(trip, 0) + 1
        lang = r.get("_lang")
        if isinstance(lang, str) and lang:
            langs[lang] = langs.get(lang, 0) + 1
        created = r.get("created")
        if isinstance(created, str):
            date = created[:10]
            if not latest_date or date > latest_date:
                latest_date = date

    return {
        "traveller_types": traveller_types,
        "trip_types": trip_types,
        "languages": langs,
        "latest_review_date": latest_date,
    }


def prepare_hotel_for_llm(hotel: HotelFull) -> dict[str, Any]:
    """Prepare hotel data for LLM scoring with richer, decision-grade fields."""
    raw_rates = hotel.get("rates", []) or []
    rates_all = [_build_rate_info(rate) for rate in raw_rates]
    rates_info = _select_rates(rates_all, MAX_RATES_PER_HOTEL)
    rates_summary = _summarize_rates(rates_all)

    amenity_groups = hotel.get("amenity_groups", []) or []
    amenities_flat = _flatten_amenities(amenity_groups)

    room_groups = hotel.get("room_groups", []) or []
    room_groups_light = [
        {
            "room_group_id": rg.get("room_group_id"),
            "name": rg.get("name"),
            "name_struct": rg.get("name_struct"),
            "room_amenities": rg.get("room_amenities", []) or [],
            "rg_ext": rg.get("rg_ext", {}) or {},
        }
        for rg in room_groups
    ]

    description_struct = hotel.get("description_struct", []) or []
    description_trimmed = []
    for block in description_struct:
        if not isinstance(block, dict):
            continue
        description_trimmed.append(
            {
                "title": block.get("title"),
                "paragraphs": _trim_paragraphs(block.get("paragraphs", []) or [], MAX_DESCRIPTION_PARAGRAPH_LENGTH),
            }
        )

    policy_struct = hotel.get("policy_struct", []) or []
    policy_trimmed = []
    for block in policy_struct:
        if not isinstance(block, dict):
            continue
        policy_trimmed.append(
            {
                "title": block.get("title"),
                "paragraphs": _trim_paragraphs(block.get("paragraphs", []) or [], MAX_POLICY_PARAGRAPH_LENGTH),
            }
        )

    images_ext = hotel.get("images_ext", []) or []
    images_summary = _summarize_images(images_ext)

    hr = hotel.get("reviews", {}) or {}
    raw_reviews = hr.get("reviews", []) if isinstance(hr, dict) else []
    reviews_sample = _build_review_sample(raw_reviews)
    reviews_meta = _summarize_review_meta(raw_reviews)

    reviews_data = {
        "total_reviews": hr.get("total_reviews", 0) if isinstance(hr, dict) else 0,
        "avg_rating": hr.get("avg_rating") if isinstance(hr, dict) else None,
        "detailed_averages": hr.get("detailed_averages", {}) if isinstance(hr, dict) else {},
        "sample_reviews": reviews_sample,
        "meta": reviews_meta,
    }

    return {
        "hotel_id": hotel.get("id", ""),
        "hid": hotel.get("hid"),
        "name": hotel.get("name", ""),
        "stars": hotel.get("star_rating", 0),
        "kind": hotel.get("kind", ""),
        "hotel_chain": hotel.get("hotel_chain"),
        "address": hotel.get("address", ""),
        "postal_code": hotel.get("postal_code"),
        "region": hotel.get("region", {}) or {},
        "latitude": hotel.get("latitude"),
        "longitude": hotel.get("longitude"),
        "phone": hotel.get("phone"),
        "email": hotel.get("email"),
        "check_in_time": hotel.get("check_in_time"),
        "check_out_time": hotel.get("check_out_time"),
        "front_desk_time_start": hotel.get("front_desk_time_start"),
        "front_desk_time_end": hotel.get("front_desk_time_end"),
        "is_closed": hotel.get("is_closed"),
        "deleted": hotel.get("deleted"),
        "is_gender_specification_required": hotel.get("is_gender_specification_required"),
        "payment_methods": hotel.get("payment_methods", []) or [],
        "star_certificate": hotel.get("star_certificate", {}) or {},
        "facts": hotel.get("facts", {}) or {},
        "serp_filters": hotel.get("serp_filters", []) or [],
        "description": description_trimmed,
        "policy_struct": policy_trimmed,
        "metapolicy_struct": hotel.get("metapolicy_struct", {}) or {},
        "metapolicy_extra_info": hotel.get("metapolicy_extra_info", ""),
        "keys_pickup": hotel.get("keys_pickup", {}) or {},
        "amenity_groups": amenity_groups,
        "amenities": amenities_flat[:MAX_AMENITIES_PER_HOTEL],
        "room_groups": room_groups_light,
        "images_summary": images_summary,
        "rates": rates_info,
        "rates_summary": rates_summary,
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
    prompt_template = _load_scoring_prompt()
    return prompt_template.format(
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
