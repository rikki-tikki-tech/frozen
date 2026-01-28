"""Hotel data processing, filtering, and pre-scoring."""

from collections.abc import Awaitable, Callable
from typing import Any

from etg import AsyncETGClient, ETGClient, Hotel, HotelContent

# Callback type: (batch_num, total_batches, hotels_loaded, total_hotels) -> None
ContentProgressCallback = Callable[[int, int, int, int], Awaitable[None]]

CONTENT_BATCH_SIZE = 100

# Country code to Ostrovok URL path mapping
COUNTRY_URL_MAP = {
    "DE": "germany", "RU": "russia", "FR": "france", "IT": "italy", "ES": "spain",
    "GB": "united_kingdom", "US": "usa", "CN": "china", "JP": "japan", "TH": "thailand",
    "AE": "uae", "TR": "turkey", "GR": "greece", "AT": "austria", "CH": "switzerland",
    "NL": "netherlands", "BE": "belgium", "PT": "portugal", "CZ": "czech_republic",
    "PL": "poland", "HU": "hungary", "SE": "sweden", "NO": "norway", "DK": "denmark",
    "FI": "finland", "IE": "ireland", "AU": "australia", "NZ": "new_zealand",
}


def get_ostrovok_url(hotel_id: str, hid: int, city: str, country_code: str) -> str:
    """Generate Ostrovok hotel URL."""
    country = COUNTRY_URL_MAP.get(country_code, country_code.lower())
    city_slug = city.lower().replace(" ", "_")
    return f"https://ostrovok.ru/hotel/{country}/{city_slug}/mid{hid}/{hotel_id}/"


def get_hotel_nights(hotel: Hotel) -> int:
    """Get number of nights from daily_prices."""
    rates = hotel.get("rates", [])
    if rates:
        daily_prices = rates[0].get("daily_prices", [])
        if daily_prices:
            return len(daily_prices)
    return 1


def get_hotel_price(hotel: Hotel) -> float | None:
    """Extract average price per night from cheapest rate's daily_prices."""
    rates = hotel.get("rates", [])
    if not rates:
        return None

    cheapest_rate = None
    min_price = float('inf')

    for rate in rates:
        payment_types = rate.get("payment_options", {}).get("payment_types", [])
        if payment_types:
            try:
                price = float(payment_types[0].get("show_amount", 0))
                if price > 0 and price < min_price:
                    min_price = price
                    cheapest_rate = rate
            except (ValueError, TypeError):
                continue

    if cheapest_rate is None:
        return None

    daily_prices = cheapest_rate.get("daily_prices", [])
    if not daily_prices:
        return None

    prices = []
    for p in daily_prices:
        try:
            price = float(p)
            if price > 0:
                prices.append(price)
        except (ValueError, TypeError):
            continue

    if not prices:
        return None

    return sum(prices) / len(prices)


def get_hotel_price_per_night(hotel: Hotel) -> float | None:
    """Extract average price per night from cheapest rate."""
    return get_hotel_price(hotel)


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


def fetch_hotel_content(
    client: ETGClient,
    hids: list[int],
    language: str,
) -> dict[int, HotelContent]:
    """Fetch content for hotels in batches."""
    content_map: dict[int, HotelContent] = {}

    for i in range(0, len(hids), CONTENT_BATCH_SIZE):
        batch = hids[i : i + CONTENT_BATCH_SIZE]
        content = client.get_hotel_content(hids=batch, language=language)
        for hotel in content:
            content_map[hotel["hid"]] = hotel

    return content_map


async def fetch_hotel_content_async(
    client: AsyncETGClient,
    hids: list[int],
    language: str,
    on_progress: ContentProgressCallback | None = None,
) -> dict[int, HotelContent]:
    """Fetch content for hotels in batches (async)."""
    content_map: dict[int, HotelContent] = {}
    total = len(hids)
    total_batches = (total + CONTENT_BATCH_SIZE - 1) // CONTENT_BATCH_SIZE

    for batch_num, i in enumerate(range(0, total, CONTENT_BATCH_SIZE), 1):
        batch = hids[i : i + CONTENT_BATCH_SIZE]
        content = await client.get_hotel_content(hids=batch, language=language)
        for hotel in content:
            content_map[hotel["hid"]] = hotel

        if on_progress:
            await on_progress(batch_num, total_batches, len(content_map), total)

    return content_map


def calculate_prescore(
    hotel: dict[str, Any], reviews_data: dict[str, Any] | None = None,
) -> float:
    """
    Calculate quick pre-score for sorting before LLM.

    Score components (0-100):
    - Stars: 0-25 points (star_rating * 5)
    - Reviews ratio: 0-50 points (positive / total * 50)
    - Reviews count: 0-25 points (min(total, 25))
    """
    score = 0.0

    stars: int = hotel.get("star_rating", 0)
    score += stars * 5

    if reviews_data:
        total: int = reviews_data.get("total_reviews", 0)
        positive: int = reviews_data.get("positive_count", 0)

        if total > 0:
            score += (positive / total) * 50

        score += min(total, 25)

    return score


def presort_hotels(
    hotels: list[dict[str, Any]],
    reviews_map: dict[int, dict[str, Any]],
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Sort hotels by pre-score and return top N."""
    scored: list[dict[str, Any]] = []
    for hotel in hotels:
        hid = hotel.get("hid")
        reviews_data = reviews_map.get(hid) if hid else None
        prescore = calculate_prescore(hotel, reviews_data)
        hotel["prescore"] = prescore
        scored.append(hotel)

    scored.sort(key=lambda h: h.get("prescore", 0), reverse=True)

    return scored[:limit]
