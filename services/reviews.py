"""Review fetching, filtering, and aggregation."""

from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast

from etg import ETGAPIError, ETGClient

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


class HotelReviewsFiltered(TypedDict):
    """Filtered hotel reviews with aggregated ratings.

    Contains average rating and detailed category averages computed
    from all reviews, plus recent reviews filtered by age.
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
) -> dict[int, list[ReviewDict]]:
    """Fetch reviews for hotels in multiple languages."""
    languages = BASE_REVIEW_LANGUAGES.copy()
    if language not in languages:
        languages.append(language)

    reviews_map: dict[int, list[ReviewDict]] = {}

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
        except ETGAPIError:
            continue

    return reviews_map


def _avg(total: float, count: int) -> float | None:
    """Calculate average or return None if no data."""
    return round(total / count, 1) if count else None


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
    reviews_map: dict[int, list[ReviewDict]],
    max_age_years: int = DEFAULT_MAX_AGE_YEARS,
    max_reviews: int = DEFAULT_MAX_REVIEWS,
) -> dict[int, HotelReviewsFiltered]:
    """Filter reviews: compute averages first, then filter by age.

    1. Compute average rating and detailed averages from ALL reviews
    2. Filter out reviews older than max_age_years
    3. Keep up to max_reviews most recent reviews
    """
    filtered_map: dict[int, HotelReviewsFiltered] = {}

    for hid, reviews in reviews_map.items():
        total_reviews = len(reviews)

        # Compute averages from ALL reviews (before filtering)
        avg_rating = None
        if reviews:
            ratings = [r["rating"] for r in reviews if r.get("rating") is not None]
            if ratings:
                avg_rating = round(sum(ratings) / len(ratings), 1)

        detailed_averages = _compute_detailed_averages(reviews)

        # Filter by age (after computing averages)
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
            "avg_rating": avg_rating,
            "detailed_averages": detailed_averages,
        }

    return filtered_map
