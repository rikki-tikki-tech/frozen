"""Review fetching, filtering, and aggregation."""

from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast

from etg import ETGAPIError, ETGClient

# Type alias for review dict (API data + custom fields)
ReviewDict = dict[str, Any]

REVIEWS_BATCH_SIZE = 100
BASE_REVIEW_LANGUAGES = ["ru", "en"]

DEFAULT_MAX_AGE_YEARS = 5
DEFAULT_MAX_REVIEWS = 500

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
    """Hotel reviews with aggregated ratings.

    Contains average rating and detailed category averages computed
    from ALL reviews, plus review list (filtered or unfiltered).
    """

    reviews: list[ReviewDict]
    total_reviews: int
    avg_rating: float | None
    detailed_averages: DetailedAverages


async def batch_get_reviews(
    client: ETGClient,
    hotel_ids: list[int],
    language: str,
) -> dict[int, HotelReviews]:
    """Fetch reviews for hotels in multiple languages and compute aggregated ratings.

    Returns reviews with avg_rating and detailed_averages computed from ALL reviews.
    """
    languages = BASE_REVIEW_LANGUAGES.copy()
    if language not in languages:
        languages.append(language)

    reviews_map: dict[int, list[ReviewDict]] = {}

    for language_code in languages:
        try:
            for i in range(0, len(hotel_ids), REVIEWS_BATCH_SIZE):
                hotel_id_batch = hotel_ids[i : i + REVIEWS_BATCH_SIZE]
                hotel_reviews_batch = await client.get_hotel_reviews(
                    hotel_ids=hotel_id_batch,
                    language=language_code,
                )

                for hotel_data in hotel_reviews_batch:
                    hid = hotel_data["hid"]
                    reviews_list = cast("list[ReviewDict]", hotel_data["reviews"])

                    for review in reviews_list:
                        review["_lang"] = language_code

                    if hid not in reviews_map:
                        reviews_map[hid] = []
                    reviews_map[hid].extend(reviews_list)
        except ETGAPIError:
            continue

    # Compute ratings for each hotel
    result: dict[int, HotelReviews] = {}
    for hid, reviews in reviews_map.items():
        avg_rating, detailed_averages = _compute_ratings(reviews)

        result[hid] = {
            "reviews": reviews,
            "total_reviews": len(reviews),
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
    rating_fields = (
        "cleanness",
        "location",
        "price",
        "services",
        "room",
        "meal",
        "wifi",
        "hygiene",
    )
    rating_sums: dict[str, float] = dict.fromkeys(rating_fields, 0.0)
    rating_counts: dict[str, int] = dict.fromkeys(rating_fields, 0)

    for review in reviews:
        detailed = review.get("detailed_review")
        if not detailed:
            continue

        # Numeric fields (0 means not rated)
        for field in ("cleanness", "location", "price", "services", "room", "meal"):
            value = detailed.get(field)
            if isinstance(value, int | float) and value > 0:
                rating_sums[field] += float(value)
                rating_counts[field] += 1

        # String fields
        wifi_str = detailed.get("wifi")
        if wifi_str and wifi_str in WIFI_SCORES:
            rating_sums["wifi"] += WIFI_SCORES[wifi_str]
            rating_counts["wifi"] += 1

        hygiene_str = detailed.get("hygiene")
        if hygiene_str and hygiene_str in HYGIENE_SCORES:
            rating_sums["hygiene"] += HYGIENE_SCORES[hygiene_str]
            rating_counts["hygiene"] += 1

    return {
        "cleanness": _avg(rating_sums["cleanness"], rating_counts["cleanness"]),
        "location": _avg(rating_sums["location"], rating_counts["location"]),
        "price": _avg(rating_sums["price"], rating_counts["price"]),
        "services": _avg(rating_sums["services"], rating_counts["services"]),
        "room": _avg(rating_sums["room"], rating_counts["room"]),
        "meal": _avg(rating_sums["meal"], rating_counts["meal"]),
        "wifi": _avg(rating_sums["wifi"], rating_counts["wifi"]),
        "hygiene": _avg(rating_sums["hygiene"], rating_counts["hygiene"]),
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
    """
    filtered_map: dict[int, HotelReviews] = {}

    for hid, hotel_reviews_data in reviews_map.items():
        reviews = hotel_reviews_data["reviews"]
        total_reviews = hotel_reviews_data["total_reviews"]

        # Filter by age
        cutoff_date = (datetime.now(tz=UTC) - timedelta(days=max_age_years * 365)).isoformat()
        recent_reviews = [r for r in reviews if r["created"] >= cutoff_date]

        # Sort by date and limit
        recent_reviews.sort(key=lambda x: x["created"], reverse=True)
        recent_reviews = recent_reviews[:max_reviews]

        filtered_map[hid] = {
            "reviews": recent_reviews,
            "total_reviews": total_reviews,
            "avg_rating": hotel_reviews_data["avg_rating"],
            "detailed_averages": hotel_reviews_data["detailed_averages"],
        }

    return filtered_map
