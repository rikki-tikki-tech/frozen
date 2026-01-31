"""Review fetching, filtering, and aggregation."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast

from etg import ETGAPIError, ETGClient

logger = logging.getLogger(__name__)

# Type alias for review dict (API data + custom fields)
ReviewDict = dict[str, Any]

REVIEWS_BATCH_SIZE = 100
BASE_REVIEW_LANGUAGES = ["ru", "en"]

DEFAULT_MAX_AGE_YEARS = 5
DEFAULT_MAX_REVIEWS = 50

# Mapping for string values in detailed_review
WIFI_SCORES: dict[str, float] = {
    "perfect": 10.0,
    "good": 8.0,
    "average": 6.0,
    "poor": 4.0,
    "bad": 2.0,
}

HYGIENE_SCORES: dict[str, float] = {
    "perfect": 10.0,
    "good": 8.0,
    "average": 6.0,
    "poor": 4.0,
    "bad": 2.0,
}


class DetailedAverages(TypedDict):
    """Average scores for detailed review categories."""

    cleanness: float | None
    location: float | None
    price: float | None
    services: float | None
    room: float | None
    meal: float | None
    wifi: float | None
    hygiene: float | None


class HotelReviews(TypedDict):
    """Hotel reviews with aggregated ratings and optional filtering stats.

    Contains average rating and detailed category averages computed
    from ALL reviews, plus review list (filtered or unfiltered) and stats.
    """

    reviews: list[ReviewDict]
    total_reviews: int
    filtered_by_age: int
    avg_rating: float | None
    detailed_averages: DetailedAverages


async def batch_get_reviews(
    client: ETGClient,
    hids: list[int],
    language: str,
) -> dict[int, HotelReviews]:
    """Fetch reviews for hotels in multiple languages and compute aggregated ratings.

    Returns reviews with avg_rating and detailed_averages computed from ALL reviews.
    filtered_by_age is set to 0 (no filtering applied yet).
    """
    languages = BASE_REVIEW_LANGUAGES.copy()
    if language not in languages:
        languages.append(language)

    reviews_map: dict[int, list[ReviewDict]] = {}
    failed_languages: list[str] = []

    for lang in languages:
        try:
            for i in range(0, len(hids), REVIEWS_BATCH_SIZE):
                batch = hids[i : i + REVIEWS_BATCH_SIZE]
                hotels_reviews = await client.get_hotel_reviews(hids=batch, language=lang)

                for hotel_data in hotels_reviews:
                    hid = hotel_data["hid"]
                    reviews_list = cast("list[ReviewDict]", hotel_data["reviews"])

                    for r in reviews_list:
                        r["_lang"] = lang

                    if hid not in reviews_map:
                        reviews_map[hid] = []
                    reviews_map[hid].extend(reviews_list)
        except ETGAPIError as e:
            failed_languages.append(lang)
            logger.warning("Reviews fetch failed for language '%s': %s", lang, e)
            continue

    if failed_languages:
        logger.warning(
            "Reviews fetching: languages %s failed, got reviews for %d hotels",
            failed_languages,
            len(reviews_map),
        )

    # Compute ratings for each hotel
    result: dict[int, HotelReviews] = {}
    for hid, reviews in reviews_map.items():
        avg_rating, detailed_averages = _compute_ratings(reviews)

        result[hid] = {
            "reviews": reviews,
            "total_reviews": len(reviews),
            "filtered_by_age": 0,  # No filtering applied yet
            "avg_rating": avg_rating,
            "detailed_averages": detailed_averages,
        }

    return result


def _avg(total: float, count: int) -> float | None:
    """Calculate average or return None if no data."""
    return round(total / count, 1) if count else None


def _compute_ratings(reviews: list[ReviewDict]) -> tuple[float | None, DetailedAverages]:
    """Compute avg_rating and detailed_averages from reviews.

    Args:
        reviews: List of review dictionaries.

    Returns:
        Tuple of (avg_rating, detailed_averages).
    """
    avg_rating = None
    if reviews:
        ratings = [r["rating"] for r in reviews if r.get("rating") is not None]
        if ratings:
            avg_rating = round(sum(ratings) / len(ratings), 1)

    detailed_averages = _compute_detailed_averages(reviews)
    return avg_rating, detailed_averages


def _compute_detailed_averages(reviews: list[ReviewDict]) -> DetailedAverages:
    """Compute average scores for detailed review categories."""
    fields = ("cleanness", "location", "price", "services", "room", "meal", "wifi", "hygiene")
    sums: dict[str, float] = dict.fromkeys(fields, 0.0)
    counts: dict[str, int] = dict.fromkeys(fields, 0)

    for review in reviews:
        detailed = review.get("detailed_review")
        if not detailed:
            continue

        # Numeric fields (0 means not rated)
        for field in ("cleanness", "location", "price", "services", "room", "meal"):
            value = detailed.get(field)
            if isinstance(value, int | float) and value > 0:
                sums[field] += float(value)
                counts[field] += 1

        # String fields
        wifi_str = detailed.get("wifi")
        if wifi_str and wifi_str in WIFI_SCORES:
            sums["wifi"] += WIFI_SCORES[wifi_str]
            counts["wifi"] += 1

        hygiene_str = detailed.get("hygiene")
        if hygiene_str and hygiene_str in HYGIENE_SCORES:
            sums["hygiene"] += HYGIENE_SCORES[hygiene_str]
            counts["hygiene"] += 1

    return {
        "cleanness": _avg(sums["cleanness"], counts["cleanness"]),
        "location": _avg(sums["location"], counts["location"]),
        "price": _avg(sums["price"], counts["price"]),
        "services": _avg(sums["services"], counts["services"]),
        "room": _avg(sums["room"], counts["room"]),
        "meal": _avg(sums["meal"], counts["meal"]),
        "wifi": _avg(sums["wifi"], counts["wifi"]),
        "hygiene": _avg(sums["hygiene"], counts["hygiene"]),
    }


def filter_reviews(
    reviews_map: dict[int, HotelReviews],
    max_age_years: int = DEFAULT_MAX_AGE_YEARS,
    max_reviews: int = DEFAULT_MAX_REVIEWS,
) -> dict[int, HotelReviews]:
    """Filter reviews by age and limit count.

    Takes reviews with pre-computed ratings and filters them:
    1. Filter out reviews older than max_age_years
    2. Keep up to max_reviews most recent reviews
    3. Preserve avg_rating and detailed_averages from original data
    4. Update filtered_by_age counter
    """
    filtered_map: dict[int, HotelReviews] = {}

    for hid, raw_data in reviews_map.items():
        reviews = raw_data["reviews"]
        total_reviews = raw_data["total_reviews"]

        # Filter by age
        cutoff_date = (datetime.now(tz=UTC) - timedelta(days=max_age_years * 365)).isoformat()
        recent_reviews = [r for r in reviews if r["created"] >= cutoff_date]
        filtered_by_age = total_reviews - len(recent_reviews)

        # Sort by date and limit
        recent_reviews.sort(key=lambda x: x["created"], reverse=True)
        recent_reviews = recent_reviews[:max_reviews]

        filtered_map[hid] = {
            "reviews": recent_reviews,
            "total_reviews": total_reviews,
            "filtered_by_age": filtered_by_age,
            "avg_rating": raw_data["avg_rating"],
            "detailed_averages": raw_data["detailed_averages"],
        }

    return filtered_map
