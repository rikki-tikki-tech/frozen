"""Review fetching, filtering, and segmentation."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast

from etg import AsyncETGClient, ETGAPIError, ETGClient

# Type alias for review dict (API data + custom fields)
ReviewDict = dict[str, Any]

# Callback type: (language, batch_num, total_batches, hotels_loaded, total_hotels) -> None
ReviewsProgressCallback = Callable[[str, int, int, int, int], Awaitable[None]]

REVIEWS_BATCH_SIZE = 100
BASE_REVIEW_LANGUAGES = ["ru", "en"]

DEFAULT_MAX_AGE_YEARS = 5
DEFAULT_REVIEWS_PER_SEGMENT = 30
DEFAULT_NEUTRAL_THRESHOLD = 7.0
DEFAULT_NEGATIVE_THRESHOLD = 5.0


class HotelReviewsFiltered(TypedDict):
    reviews: list[ReviewDict]
    total_reviews: int
    positive_count: int
    neutral_count: int
    negative_count: int


def fetch_reviews(
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
                hotels_reviews = client.get_hotel_reviews(hids=batch, language=lang)

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


async def fetch_reviews_async(
    client: AsyncETGClient,
    hids: list[int],
    language: str,
    on_progress: ReviewsProgressCallback | None = None,
) -> dict[int, list[ReviewDict]]:
    """Fetch reviews for hotels in multiple languages (async)."""
    languages = BASE_REVIEW_LANGUAGES.copy()
    if language not in languages:
        languages.append(language)

    reviews_map: dict[int, list[ReviewDict]] = {}
    total = len(hids)

    for lang in languages:
        total_batches = (total + REVIEWS_BATCH_SIZE - 1) // REVIEWS_BATCH_SIZE
        try:
            for batch_num, i in enumerate(range(0, total, REVIEWS_BATCH_SIZE), 1):
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

                if on_progress:
                    loaded = min((batch_num) * REVIEWS_BATCH_SIZE, total)
                    await on_progress(lang, batch_num, total_batches, loaded, total)
        except ETGAPIError:
            continue

    return reviews_map


def filter_reviews(
    reviews_map: dict[int, list[ReviewDict]],
    max_age_years: int = DEFAULT_MAX_AGE_YEARS,
    reviews_per_segment: int = DEFAULT_REVIEWS_PER_SEGMENT,
    neutral_threshold: float = DEFAULT_NEUTRAL_THRESHOLD,
    negative_threshold: float = DEFAULT_NEGATIVE_THRESHOLD,
) -> dict[int, HotelReviewsFiltered]:
    """Filter reviews by date and segment by rating."""
    cutoff_date = (datetime.now(tz=UTC) - timedelta(days=max_age_years * 365)).isoformat()

    filtered_map: dict[int, HotelReviewsFiltered] = {}

    for hid, reviews in reviews_map.items():
        total_reviews = len(reviews)

        valid_reviews = [
            r for r in reviews
            if r["created"] >= cutoff_date and r["rating"] > 0
        ]

        positive = [r for r in valid_reviews if r["rating"] >= neutral_threshold]
        neutral = [
            r for r in valid_reviews
            if negative_threshold <= r["rating"] < neutral_threshold
        ]
        negative = [r for r in valid_reviews if r["rating"] < negative_threshold]

        positive.sort(key=lambda x: x["created"], reverse=True)
        neutral.sort(key=lambda x: x["created"], reverse=True)
        negative.sort(key=lambda x: x["created"], reverse=True)

        positive = positive[:reviews_per_segment]
        neutral = neutral[:reviews_per_segment]
        negative = negative[:reviews_per_segment]

        filtered_map[hid] = {
            "reviews": positive + neutral + negative,
            "total_reviews": total_reviews,
            "positive_count": len(positive),
            "neutral_count": len(neutral),
            "negative_count": len(negative),
        }

    return filtered_map
