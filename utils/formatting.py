"""Date and guest formatting utilities for Russian UI."""

from datetime import date

from etg import GuestRoom


def format_guests(guests: list[GuestRoom]) -> str:
    """Format guests count: '2 взрослых, 2 детей' or '1 гость'."""
    adults = sum(g.get("adults", 0) for g in guests)
    children = sum(len(g.get("children", [])) for g in guests)

    parts = []
    if adults == 1:
        parts.append("1 взрослый")
    elif adults > 1:
        parts.append(f"{adults} взрослых")

    if children == 1:
        parts.append("1 ребёнок")
    elif children > 1:
        parts.append(f"{children} детей")

    return ", ".join(parts) if parts else "1 гость"


def format_dates(checkin: date, checkout: date) -> str:
    """Format dates: '15–17 янв'."""
    months = [
        "янв", "фев", "мар", "апр", "мая", "июн",
        "июл", "авг", "сен", "окт", "ноя", "дек",
    ]
    m_in = months[checkin.month - 1]
    m_out = months[checkout.month - 1]

    if checkin.month == checkout.month:
        return f"{checkin.day}–{checkout.day} {m_in}"
    return f"{checkin.day} {m_in} – {checkout.day} {m_out}"
