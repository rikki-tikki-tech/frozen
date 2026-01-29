"""ETG (Emerging Travel Group) B2B API client package."""

from .client import ETGClient
from .exceptions import ETGAPIError, ETGAuthError, ETGClientError, ETGNetworkError
from .types import (
    GuestRoom,
    Hotel,
    HotelContent,
    HotelKind,
    HotelRate,
    HotelReviews,
    Region,
    Review,
    SearchResults,
)

__all__ = [
    "ETGAPIError",
    "ETGAuthError",
    "ETGClient",
    "ETGClientError",
    "ETGNetworkError",
    "GuestRoom",
    "Hotel",
    "HotelContent",
    "HotelKind",
    "HotelRate",
    "HotelReviews",
    "Region",
    "Review",
    "SearchResults",
]
