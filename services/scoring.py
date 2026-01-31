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
    selected_rate_hash: str


class HotelScore(BaseModel):
    """Individual hotel score from LLM evaluation."""

    hotel_id: str
    score: int
    top_reasons: list[str]
    score_penalties: list[str]
    selected_rate_hash: str


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

**CRITICAL OUTPUT REQUIREMENT:** You MUST return EXACTLY {top_count} hotels in the results array. Score ALL input hotels and return the top {top_count} highest-scoring ones.

## Input Data
1. **Guests:** {guests_info}
2. **Price Range:** {price_range}
3. **User Preferences:** {user_preferences}
4. **Hotels List:** {hotels_json}

## 1. Hotel Type Priority (The Hierarchy)
Use the following tiers to evaluate `hotel_kind`. Tier 1 is most desirable.
- **Tier 1 (Premium):** Castle, Resort, Boutique_and_Design, Villas_and_Bungalows, Hotel.
- **Tier 2 (Mid):** Apart-hotel, Sanatorium, Mini-hotel, Apartment, Guesthouse.
- **Tier 3 (Budget/Alt):** BNB, Glamping, Cottages_and_Houses, Farm.
- **Tier 4 (Low):** Hostel, Camping, Unspecified.

**Rule:** Tier 1 and 2 are preferred. Tier 3 and 4 should be heavily penalized (-50 points) UNLESS the user explicitly requested them or the budget is extremely low (<$30).

## 2. Core Scoring Philosophy (The "Brain")
**CRITICAL:** Do NOT simply rank by lowest price.

**A. The "Anti-Downgrade" Star Rule**
Scan the market prices first.
- **If 4-5 Star hotels are within budget:** Immediately disqualify or severely penalize (-25 pts) any hotel with **3 stars or less**.
- **If 3-4 Star hotels are within budget:** Immediately disqualify or penalize any hotel with **2 stars or less**.
*Logic: Do not recommend a "downgrade" if quality is affordable.*

**B. Guest Rating Priority (Quality Assurance)**
Guest reviews are the ultimate truth detector.
- **High Standards:** A hotel CANNOT receive a high score (>85) if its guest rating is below **8.0**, regardless of price or stars.
- **The "Trap" Penalty:** A high-star hotel (4-5*) with a low guest rating (<7.0) is a "trap." Apply a severe penalty (-40 points).
- **Safe Zone:** Prioritize hotels with ratings **8.5+** as they guarantee user satisfaction.

**C. Value for Money**
A $200 Tier 1 (Resort) is better than a $100 Tier 4 (Hostel). Award higher scores to high-tier properties if the price is reasonable.

**D. Preference Matching**
- **Boost:** Points for explicit amenities (e.g., User: "pool" -> Hotel: "has pool").
- **Penalty:** Deductions for missing explicit needs.

## 3. Field Content Guidelines
Generate the response based on the provided schema. Follow these specific instructions:

### `hotel_id` (string)
The unique identifier of the hotel from the input data.

### `selected_rate_hash` (string) - MANDATORY FIELD
**CRITICAL REQUIREMENT:** Each hotel has a `rates` array. Each rate has a `match_hash` field.

**Your task:**
1. Look at ALL rates for the hotel in the `rates` array
2. Each rate has: `match_hash`, `room`, `price`, `meal`, `has_breakfast`, optionally `free_cancel_before`
3. Select the BEST rate based on:
   - **Room suitability:** Can accommodate all guests (check adults + children counts)
   - **Meal preferences:** If user wants breakfast, pick rate with `has_breakfast: true`
   - **Cancellation:** Prefer `free_cancel_before` when available
   - **Value:** Balance price with amenities (slightly pricier with breakfast may be better value)

**CRITICAL:** Copy the EXACT `match_hash` string from the selected rate. Do NOT make up or modify this value.

**Example input:**
```json
{{
  "hotel_id": "example_hotel",
  "rates": [
    {{"match_hash": "abc123", "room": "Standard", "price": "5000 RUB", "meal": "nomeal"}},
    {{"match_hash": "xyz789", "room": "Family Suite", "price": "8000 RUB", "meal": "breakfast", "has_breakfast": true}}
  ]
}}
```
**Example output for this hotel:** `"selected_rate_hash": "xyz789"` (if family suite with breakfast is better for user)

### `score` (integer 0-100)
- **90-100:** Tier 1/2, High Stars, **Guest Rating 9.0+**, Great Price.
- **70-89:** Good Tier, **Guest Rating 8.0+**, acceptable trade-offs.
- **50-69:** Average ratings (7.0-7.9), Lower Tier, or lower stars than market average.
- **0-49:** Rejected due to **Low Guest Rating (<7.0)**, "Anti-Downgrade" rule, or Tier 4.

### `top_reasons` (list of strings)
Be specific about Value, Tier, and Rating.
- **Format:** [Tier/Star Advantage], [Rating Highlight], [Value Deal].
- **Examples:**
  - "Premium 'Resort' type (Tier 1) for a mid-range price"
  - "Exceptional guest rating (9.5/10) guarantees quality"
  - "4-Star service significantly better than cheaper options"

### `score_penalties` (list of strings)
Explain exactly why the score is not 100.
- **Format:** [Rating Issue], [Tier/Star Issue], [Missing Feature].
- **Examples:**
  - "Guest rating is too low (6.8/10) for this price"
  - "Low priority type: Hostel (Tier 4)"
  - "Only 2 stars (Market offers 4 stars at this price)"

### `type` (strings)
Specify the type of accommodation.
- **Examples:**
    - Resort
    - Hotel
    - Hostel

### `cost` (strings)
Specify the cost per night with the currency and add cost per stay.
- **Examples:**
    - $400 for your 4-nights stay ($100 per night)
    - €1200 for your 6-nights stay (€200 per night)

### `location` (strings)
Specify the location of accommodation.
- **Examples:**
    - 1km from the beach
    - 11km from the city center

### `rooms` (strings)
Specify the location of accommodation.
- **Examples:**
    - 2 double rooms for you family (2 adults and 2 kids)
    - 2-rooms apartment

### `rating` (strings)
Specify the guest rating.
- **Examples:**
    - 7.8/10
    - 8.5/10

### `summary` (string)
Produce a **strategic market overview** in **4–6 sentences**.

**Must include:**
- A high-level description of the market: median price, primary hotel locations (city center vs outside), and the **dominant accommodation type (hotels, hostels, apartments).
- The price range analyzed.
- A clear explanation that the selection prioritized properties with the strong cost–value balance, higher star ratings, and high guest ratings. Do not mention Tiers.

**Rejection transparency (mandatory):**
- When referencing rejected properties, explicitly state that this is the example and mention the hotel Name, ID, and Guest Rating, and briefly explain why they were excluded (e.g., poor rating, weak value versus higher-tier alternatives).

**Positioning:**
- Conclude by reinforcing that the final recommendations emphasize highly rated, best-value Tier 1/2 accommodations** over cheaper but lower-quality options.

**Example phrasing pattern (illustrative only):**  
"Most of the accommodations in city X are centrally located apartments, with a median price of $150. We analyzed options ranging from $50 to $500. As an example: 'Budget Hostel' (ID: 123, rating 5.5/10) was excluded due to low guest satisfaction despite its low price. 'City Inn' (ID: 456, 2★, rating 6.8) was rejected because for a slightly higher cost, higher-tier properties offer significantly better value. The final selection focuses on the hotels with strong guest ratings and superior overall quality."

## 4. Final Selection & Output
**CRITICAL:** You MUST score EVERY SINGLE hotel from the input list. Do not skip or ignore any hotels.

After evaluating all provided hotels:
1. **Score every hotel (mandatory):**
   Assign a numeric score from **0 to 100** to **every hotel** in the input list.
   Hotels with similar accommodation types or amenities **may receive similar scores**.
2. **Sort (required):**
   Rank all hotels by `score` in **descending order** (highest score first).
3. **Select TOP `{top_count}` (hard constraint):**
   The `results` array **MUST contain EXACTLY `{top_count}` hotels**, selected strictly from the highest-scoring entries.
   No more and no fewer results are allowed.
4. **Output format (strict):**
   The final output **MUST fully comply** with the required **JSON schema structure**.

**RESPONSE FORMAT EXAMPLE:**
```json
{{
  "results": [
    {{
      "hotel_id": "example_hotel_1",
      "selected_rate_hash": "abc123xyz",
      "score": 95,
      "top_reasons": ["Reason 1", "Reason 2", "Reason 3"],
      "score_penalties": ["Penalty 1"]
    }},
    ...exactly {top_count} hotels total
  ],
  "summary": "Market overview and selection rationale..."
}}
```

**REMINDER:**
- The output must include exactly {top_count} hotel objects in the `results` array
- EVERY hotel object MUST have `selected_rate_hash` copied from one of its rates
- Always fill the full quota of {top_count} hotels
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
            "match_hash": rate.get("match_hash", ""),
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
    reviews_sample = [
        {
            "id": r.get("id"),
            "rating": r.get("rating"),
            "plus": (r.get("review_plus") or "")[:REVIEW_TEXT_MAX_LENGTH],
            "minus": (r.get("review_minus") or "")[:REVIEW_TEXT_MAX_LENGTH],
        }
        for r in raw_reviews[:MAX_REVIEWS_PER_HOTEL]
    ]

    # Add aggregated review statistics
    reviews_data = {
        "total_reviews": hr.get("total_reviews", 0) if isinstance(hr, dict) else 0,
        "avg_rating": hr.get("avg_rating") if isinstance(hr, dict) else None,
        "detailed_averages": hr.get("detailed_averages", {}) if isinstance(hr, dict) else {},
        "sample_reviews": reviews_sample,
    }

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
    return SCORING_PROMPT.format(
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
    agent = _create_agent(model_name)
    top_count = min(top_count, len(hotels))

    hotels_for_llm = [prepare_hotel_for_llm(h) for h in hotels]
    prompt = _build_prompt(
        hotels_for_llm, user_preferences, guests, min_price, max_price, currency, top_count
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
