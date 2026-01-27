"""Review fetching, filtering, and segmentation."""

from datetime import datetime, timedelta
from typing import TypedDict

from etg import AsyncETGClient, ETGAPIError, ETGClient

REVIEWS_BATCH_SIZE = 100
BASE_REVIEW_LANGUAGES = ["ru", "en"]

DEFAULT_MAX_AGE_YEARS = 5
DEFAULT_REVIEWS_PER_SEGMENT = 30
DEFAULT_NEUTRAL_THRESHOLD = 7.0
DEFAULT_NEGATIVE_THRESHOLD = 5.0


class HotelReviewsFiltered(TypedDict):
    reviews: list
    total_reviews: int
    positive_count: int
    neutral_count: int
    negative_count: int


def fetch_reviews(
    client: ETGClient,
    hids: list[int],
    language: str,
) -> dict[int, list]:
    """Fetch reviews for hotels in multiple languages."""
    languages = BASE_REVIEW_LANGUAGES.copy()
    if language not in languages:
        languages.append(language)

    reviews_map: dict[int, list] = {}

    for lang in languages:
        try:
            for i in range(0, len(hids), REVIEWS_BATCH_SIZE):
                batch = hids[i : i + REVIEWS_BATCH_SIZE]
                hotels_reviews = client.get_hotel_reviews(hids=batch, language=lang)

                for hotel_data in hotels_reviews:
                    hid = hotel_data["hid"]
                    reviews = hotel_data["reviews"]

                    for r in reviews:
                        r["_lang"] = lang

                    if hid not in reviews_map:
                        reviews_map[hid] = []
                    reviews_map[hid].extend(reviews)
        except ETGAPIError:
            continue

    return reviews_map


async def fetch_reviews_async(
    client: AsyncETGClient,
    hids: list[int],
    language: str,
) -> dict[int, list]:
    """Fetch reviews for hotels in multiple languages (async)."""
    languages = BASE_REVIEW_LANGUAGES.copy()
    if language not in languages:
        languages.append(language)

    reviews_map: dict[int, list] = {}

    for lang in languages:
        try:
            for i in range(0, len(hids), REVIEWS_BATCH_SIZE):
                batch = hids[i : i + REVIEWS_BATCH_SIZE]
                hotels_reviews = await client.get_hotel_reviews(hids=batch, language=lang)

                for hotel_data in hotels_reviews:
                    hid = hotel_data["hid"]
                    reviews = hotel_data["reviews"]

                    for r in reviews:
                        r["_lang"] = lang

                    if hid not in reviews_map:
                        reviews_map[hid] = []
                    reviews_map[hid].extend(reviews)
        except ETGAPIError:
            continue

    return reviews_map


def filter_reviews(
    reviews_map: dict[int, list],
    max_age_years: int = DEFAULT_MAX_AGE_YEARS,
    reviews_per_segment: int = DEFAULT_REVIEWS_PER_SEGMENT,
    neutral_threshold: float = DEFAULT_NEUTRAL_THRESHOLD,
    negative_threshold: float = DEFAULT_NEGATIVE_THRESHOLD,
) -> dict[int, HotelReviewsFiltered]:
    """Filter reviews by date and segment by rating."""
    cutoff_date = (datetime.now() - timedelta(days=max_age_years * 365)).isoformat()

    filtered_map: dict[int, HotelReviewsFiltered] = {}

    for hid, reviews in reviews_map.items():
        total_reviews = len(reviews)

        valid_reviews = [
            r for r in reviews
            if r["created"] >= cutoff_date and r["rating"] > 0
        ]

        positive = [r for r in valid_reviews if r["rating"] >= neutral_threshold]
        neutral = [r for r in valid_reviews if negative_threshold <= r["rating"] < neutral_threshold]
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
