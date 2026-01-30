"""URL generation utilities."""

from etg import GuestRoom


def ostrovok_url(  # noqa: PLR0913
    hotel_id: str,
    hid: int,
    checkin: str,
    checkout: str,
    guests: list[GuestRoom],
    region_id: int,
    country_slug: str = "russia",
    city_slug: str = "moscow",
) -> str:
    """Generate Ostrovok booking URL for a hotel.

    Args:
        hotel_id: Hotel string ID (e.g., 'novum_hotel_aldea_berlin_centrum')
        hid: Hotel numeric ID
        checkin: Check-in date (YYYY-MM-DD)
        checkout: Check-out date (YYYY-MM-DD)
        guests: List of room configurations
        region_id: Region ID for the search
        country_slug: Country URL slug (default: 'russia')
        city_slug: City URL slug (default: 'moscow')

    Returns:
        Full Ostrovok URL for the hotel
    """
    # Convert dates from YYYY-MM-DD to DD.MM.YYYY
    ci_parts = checkin.split("-")
    co_parts = checkout.split("-")
    dates = (
        f"{ci_parts[2]}.{ci_parts[1]}.{ci_parts[0]}-"
        f"{co_parts[2]}.{co_parts[1]}.{co_parts[0]}"
    )

    # Format guests: "{adults}and{child1_age}.{child2_age}..." per room
    # Multiple rooms separated by comma
    guests_parts = []
    for room in guests:
        adults = room.get("adults", 0)
        children = room.get("children", [])
        if children:
            children_ages = ".".join(str(age) for age in children)
            guests_parts.append(f"{adults}and{children_ages}")
        else:
            guests_parts.append(str(adults))
    guests_param = ",".join(guests_parts)

    base = f"https://ostrovok.ru/hotel/{country_slug}/{city_slug}"
    return f"{base}/mid{hid}/{hotel_id}/?dates={dates}&guests={guests_param}&q={region_id}"
