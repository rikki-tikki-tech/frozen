"""ETG (Emerging Travel Group) B2B API client package."""

from .client import AsyncETGClient, ETGClient
from .exceptions import ETGAPIError, ETGAuthError, ETGClientError, ETGNetworkError
from .types import (
    GuestRoom,
    Hotel,
    HotelContent,
    HotelRate,
    HotelReviews,
    Region,
    Review,
    SearchResults,
)

__all__ = [
    "AsyncETGClient",
    "ETGAPIError",
    "ETGAuthError",
    "ETGClient",
    "ETGClientError",
    "ETGNetworkError",
    "GuestRoom",
    "Hotel",
    "HotelContent",
    "HotelRate",
    "HotelReviews",
    "Region",
    "Review",
    "SearchResults",
]
