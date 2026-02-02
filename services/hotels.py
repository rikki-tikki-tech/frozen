"""Hotel data processing, filtering, and pre-scoring."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any, TypedDict, cast

from etg import ETGAPIError, ETGClient, Hotel, HotelContent, HotelKind, HotelRate

if TYPE_CHECKING:
    from .reviews import HotelReviews
    from .scoring import HotelScoreDict

CONTENT_BATCH_SIZE = 100

# Hotel kind priority tiers (1 = most premium)
HOTEL_KIND_TIERS: dict[HotelKind, int] = {
    # Tier 1: Premium
    "Castle": 1,
    "Resort": 1,
    "Boutique_and_Design": 1,
    "Villas_and_Bungalows": 1,
    "Hotel": 1,
    # Tier 2: Mid-tier
    "Apart-hotel": 2,
    "Sanatorium": 2,
    "Mini-hotel": 2,
    "Apartment": 2,
    "Guesthouse": 2,
    # Tier 3: Budget/Alternative
    "BNB": 3,
    "Glamping": 3,
    "Cottages_and_Houses": 3,
    "Farm": 3,
    # Tier 4: Low priority
    "Hostel": 4,
    "Camping": 4,
    "Unspecified": 4,
}
DEFAULT_KIND_TIER = 4


class HotelFull(HotelContent):
    """Combined hotel data from search, content, and reviews.

    This type extends HotelContent with:
    - rates: from Hotel search result
    - reviews: filtered reviews with sentiment segmentation
    """

    rates: list[HotelRate]
    reviews: HotelReviews


class HotelScored(HotelFull):
    """Hotel with LLM scoring data.

    This type extends HotelFull with scoring fields from LLM evaluation.
    Used as the final output type after scoring is complete.
    """

    score: int
    top_reasons: list[str]
    score_penalties: list[str]
    selected_rate_hash: str | None


def combine_hotels_data(
    hotels: list[Hotel],
    content_map: dict[int, HotelContent],
    reviews_map: dict[int, HotelReviews],
) -> list[HotelFull]:
    """Combine hotel search results with content and reviews.

    Args:
        hotels: List of hotels from search.
        content_map: Map of hid to hotel content.
        reviews_map: Map of hid to filtered reviews.

    Returns:
        List of combined hotel data.
    """
    empty_reviews: HotelReviews = {
        "reviews": [],
        "total_reviews": 0,
        "avg_rating": None,
        "detailed_averages": {
            "cleanness": None,
            "location": None,
            "price": None,
            "services": None,
            "room": None,
            "meal": None,
            "wifi": None,
            "hygiene": None,
        },
    }

    combined: list[HotelFull] = []
    for hotel in hotels:
        hotel_hid = hotel["hid"]
        content = content_map.get(hotel_hid)
        reviews = reviews_map.get(hotel_hid, empty_reviews)

        hotel_data: dict[str, Any] = {**hotel, "reviews": reviews}
        if content:
            hotel_data.update(content)
        combined.append(cast("HotelFull", hotel_data))

    return combined


def get_hotel_nights(hotel: Hotel) -> int:
    """Get number of nights from daily_prices."""
    rates = hotel.get("rates", [])
    if rates:
        daily_prices = rates[0].get("daily_prices", [])
        if daily_prices:
            return len(daily_prices)
    return 1


def _get_rate_price(rate: HotelRate) -> float | None:
    """Extract total price from a rate's payment options.

    Args:
        rate: Hotel rate data.

    Returns:
        Total price or None if not available.
    """
    payment_types = rate.get("payment_options", {}).get("payment_types", [])
    if not payment_types:
        return None
    try:
        price = float(payment_types[0].get("show_amount", 0))
    except (ValueError, TypeError):
        return None
    else:
        return price if price > 0 else None


def _parse_daily_prices(daily_prices: list[str]) -> list[float]:
    """Parse daily prices list into valid float values.

    Args:
        daily_prices: List of price strings.

    Returns:
        List of valid positive prices.
    """
    prices = []
    for price_str in daily_prices:
        try:
            price = float(price_str)
            if price > 0:
                prices.append(price)
        except (ValueError, TypeError):
            continue
    return prices


def get_rate_price_per_night(rate: HotelRate) -> float | None:
    """Calculate average price per night from rate's daily_prices.

    Args:
        rate: Hotel rate data.

    Returns:
        Average price per night or None if not available.
    """
    daily_prices = rate.get("daily_prices", [])
    prices = _parse_daily_prices(daily_prices)
    if not prices:
        return None

    return sum(prices) / len(prices)


def get_hotel_price_per_night(hotel: Hotel) -> float | None:
    """Extract average price per night from cheapest rate's daily_prices.

    Args:
        hotel: Hotel data dictionary.

    Returns:
        Average price per night or None if not available.
    """
    rates = hotel.get("rates", [])
    if not rates:
        return None

    # Find cheapest rate
    cheapest_rate = None
    min_price = float("inf")
    for rate in rates:
        price = _get_rate_price(rate)
        if price is not None and price < min_price:
            min_price = price
            cheapest_rate = rate

    if cheapest_rate is None:
        return None

    return get_rate_price_per_night(cheapest_rate)


def filter_hotels_by_price(
    hotels: list[Hotel],
    min_price_per_night: float | None = None,
    max_price_per_night: float | None = None,
) -> list[Hotel]:
    """Filter hotels by price per night range."""
    if min_price_per_night is None and max_price_per_night is None:
        return hotels

    filtered = []
    for hotel in hotels:
        price_per_night = get_hotel_price_per_night(hotel)
        if price_per_night is None:
            continue
        if min_price_per_night is not None and price_per_night < min_price_per_night:
            continue
        if max_price_per_night is not None and price_per_night > max_price_per_night:
            continue
        filtered.append(hotel)

    return filtered


def filter_rates_by_price(
    rates: list[HotelRate],
    min_price: float | None = None,
    max_price: float | None = None,
) -> list[HotelRate]:
    """Filter rates by price per night range.

    Args:
        rates: List of hotel rates.
        min_price: Minimum price per night (or None).
        max_price: Maximum price per night (or None).

    Returns:
        Filtered list of rates.
    """
    if min_price is None and max_price is None:
        return rates

    filtered: list[HotelRate] = []
    for rate in rates:
        price_per_night = get_rate_price_per_night(rate)
        if price_per_night is None:
            continue
        if min_price is not None and price_per_night < min_price:
            continue
        if max_price is not None and price_per_night > max_price:
            continue
        filtered.append(rate)

    return filtered


MAX_HOTELS_FOR_ANALYSIS = 500


class SampleHotelsResult(TypedDict):
    """Result of sample_hotels function."""

    hotels: list[Hotel]
    sampled: int | None


def sample_hotels(
    hotels: list[Hotel],
    max_count: int = MAX_HOTELS_FOR_ANALYSIS,
) -> SampleHotelsResult:
    """Sample hotels if there are too many.

    Args:
        hotels: List of hotels to sample.
        max_count: Maximum number of hotels to keep.

    Returns:
        SampleHotelsResult with sampled hotels and count.
    """
    if len(hotels) <= max_count:
        return {"hotels": hotels, "sampled": None}

    return {
        "hotels": random.sample(hotels, max_count),
        "sampled": max_count,
    }


async def batch_get_content(
    client: ETGClient,
    hotel_ids: list[int],
    language: str,
) -> dict[int, HotelContent]:
    """Fetch hotel content in batches.

    Args:
        client: ETG API client.
        hotel_ids: List of hotel IDs to fetch content for.
        language: Response language code.

    Returns:
        Mapping of hotel ID to hotel content.
    """
    content_map: dict[int, HotelContent] = {}

    for i in range(0, len(hotel_ids), CONTENT_BATCH_SIZE):
        hotel_id_batch = hotel_ids[i : i + CONTENT_BATCH_SIZE]
        try:
            content = await client.get_hotel_content(hotel_ids=hotel_id_batch, language=language)
            for hotel in content:
                content_map[hotel["hid"]] = hotel
        except ETGAPIError:
            continue

    return content_map


def calculate_prescore(
    hotel: HotelFull, reviews_data: HotelReviews | None = None,
) -> float:
    """Calculate quick pre-score for sorting before LLM.

    Score components (0-100):
    - Stars: 0-25 points (star_rating * 5)
    - Avg rating: 0-50 points (avg_rating / 10 * 50)
    - Reviews count: 0-25 points (min(total, 25))

    Args:
        hotel: Combined hotel data.
        reviews_data: Filtered reviews data.

    Returns:
        Pre-score value (0-100).
    """
    score = 0.0

    stars = hotel.get("star_rating", 0)
    score += stars * 5

    if reviews_data:
        total = reviews_data.get("total_reviews", 0)
        avg_rating = reviews_data.get("avg_rating")

        if avg_rating is not None:
            score += (avg_rating / 10) * 50

        score += min(total, 25)

    return score


def _get_hotel_tier(hotel: HotelFull) -> int:
    """Get priority tier for a hotel based on its kind."""
    kind = hotel.get("kind", "Unspecified")
    return HOTEL_KIND_TIERS.get(kind, DEFAULT_KIND_TIER)


class _ScoredHotel(TypedDict):
    """Internal type for presort with scoring metadata."""

    hotel: HotelFull
    prescore: float
    tier: int


def presort_hotels(
    hotels: list[HotelFull],
    reviews_map: dict[int, HotelReviews],
    limit: int = 100,
) -> list[HotelFull]:
    """Sort hotels by kind tier and pre-score, return top N.

    Hotels are grouped into 4 tiers by property type (premium first).
    Within each tier, hotels are sorted by prescore. If a higher tier
    has enough hotels to fill the limit, lower tiers are not included.
    """
    # Calculate prescore and tier for each hotel
    scored: list[_ScoredHotel] = []
    for hotel in hotels:
        hotel_hid = hotel.get("hid")
        reviews_data = reviews_map.get(hotel_hid) if hotel_hid else None
        prescore = calculate_prescore(hotel, reviews_data)
        tier = _get_hotel_tier(hotel)
        scored.append({"hotel": hotel, "prescore": prescore, "tier": tier})

    # Group by tier
    tiers: dict[int, list[_ScoredHotel]] = {1: [], 2: [], 3: [], 4: []}
    for item in scored:
        tiers[item["tier"]].append(item)

    # Sort each tier by prescore
    for tier_hotels in tiers.values():
        tier_hotels.sort(key=lambda h: h["prescore"], reverse=True)

    # Fill result from tiers in priority order
    result: list[HotelFull] = []
    for tier_num in (1, 2, 3, 4):
        if len(result) >= limit:
            break
        remaining = limit - len(result)
        result.extend(item["hotel"] for item in tiers[tier_num][:remaining])

    return result


def _get_valid_rate_hash(hotel: HotelFull, selected_hash: str | None) -> str | None:
    """Validate selected_rate_hash from LLM.

    Args:
        hotel: Hotel data with rates.
        selected_hash: Hash returned by LLM (can be None).

    Returns:
        Valid rate hash if found, None if invalid or no rates available.
    """
    if selected_hash is None:
        return None

    rates = hotel.get("rates", [])
    if not rates:
        return None  # No rates available

    valid_hashes = {rate.get("match_hash", "") for rate in rates}
    if selected_hash in valid_hashes:
        return selected_hash

    # Invalid hash - return None instead of fallback
    return None


def finalize_scored_hotels(
    hotels: list[HotelFull],
    scoring_results: list[HotelScoreDict],
) -> list[HotelScored]:
    """Merge hotel data with scoring results in score order.

    Returns hotels in the order from scoring_results (sorted by score desc),
    with hotel data merged from hotels list.

    Validates selected_rate_hash against hotel's available rates.
    If LLM returned invalid hash, falls back to first rate.

    Args:
        hotels: List of hotel data with content and reviews.
        scoring_results: List of LLM scoring results sorted by score.

    Returns:
        List of scored hotels in score order.
    """
    hotels_map: dict[str, HotelFull] = {h["id"]: h for h in hotels}

    result: list[HotelScored] = []
    for score_data in scoring_results:
        hotel_id = score_data["hotel_id"]
        hotel = hotels_map.get(hotel_id)
        if hotel is None:
            continue

        # Validate and potentially fix the rate hash
        valid_hash = _get_valid_rate_hash(hotel, score_data["selected_rate_hash"])

        scored_hotel: dict[str, Any] = {
            **hotel,
            "score": score_data["score"],
            "top_reasons": score_data["top_reasons"],
            "score_penalties": score_data["score_penalties"],
            "selected_rate_hash": valid_hash,
        }
        result.append(cast("HotelScored", scored_hotel))

    return result
