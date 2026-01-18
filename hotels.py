from etg_client import AsyncETGClient, ETGClient, Hotel, HotelContent

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


def get_hotel_price(hotel: Hotel) -> float | None:
    """Extract minimum price from hotel rates."""
    rates = hotel.get("rates", [])
    if rates:
        payment_types = rates[0].get("payment_options", {}).get("payment_types", [])
        if payment_types:
            try:
                return float(payment_types[0].get("show_amount", 0))
            except (ValueError, TypeError):
                return None
    return None


def filter_hotels_by_price(
    hotels: list[Hotel],
    min_price: float | None = None,
    max_price: float | None = None,
) -> list[Hotel]:
    """Filter hotels by price range."""
    if min_price is None and max_price is None:
        return hotels

    filtered = []
    for hotel in hotels:
        price = get_hotel_price(hotel)
        if price is None:
            continue
        if min_price is not None and price < min_price:
            continue
        if max_price is not None and price > max_price:
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
) -> dict[int, HotelContent]:
    """Fetch content for hotels in batches (async)."""
    content_map: dict[int, HotelContent] = {}

    for i in range(0, len(hids), CONTENT_BATCH_SIZE):
        batch = hids[i : i + CONTENT_BATCH_SIZE]
        content = await client.get_hotel_content(hids=batch, language=language)
        for hotel in content:
            content_map[hotel["hid"]] = hotel

    return content_map


def calculate_prescore(hotel: dict, reviews_data: dict | None = None) -> float:
    """
    Calculate quick pre-score for sorting before LLM.

    Score components (0-100):
    - Stars: 0-25 points (star_rating * 5)
    - Reviews ratio: 0-50 points (positive / total * 50)
    - Reviews count: 0-25 points (min(total, 25))
    """
    score = 0.0

    # Stars (0-25)
    stars = hotel.get("star_rating", 0)
    score += stars * 5

    # Reviews data
    if reviews_data:
        total = reviews_data.get("total_reviews", 0)
        positive = reviews_data.get("positive_count", 0)

        # Positive ratio (0-50)
        if total > 0:
            score += (positive / total) * 50

        # Reviews count bonus (0-25)
        score += min(total, 25)

    return score


def presort_hotels(
    hotels: list[dict],
    reviews_map: dict[int, dict],
    limit: int = 100,
) -> list[dict]:
    """
    Sort hotels by pre-score and return top N.

    Args:
        hotels: List of hotel dicts (with content merged)
        reviews_map: Reviews data by hid
        limit: Maximum number of hotels to return

    Returns:
        Top hotels sorted by pre-score (descending)
    """
    # Calculate prescore for each hotel
    scored = []
    for hotel in hotels:
        hid = hotel.get("hid")
        reviews_data = reviews_map.get(hid) if hid else None
        prescore = calculate_prescore(hotel, reviews_data)
        hotel["prescore"] = prescore
        scored.append(hotel)

    # Sort by prescore descending
    scored.sort(key=lambda h: h.get("prescore", 0), reverse=True)

    return scored[:limit]
