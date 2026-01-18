"""
ETG (Emerging Travel Group / Ostrovok) B2B API v3 Client

Provides a clean interface to interact with the ETG hotel booking API.
Uses httpx for HTTP requests with Basic Authentication.

API Documentation: https://docs.emergingtravel.com/docs/
"""

import time
from typing import NotRequired, TypedDict

import httpx


# =============================================================================
# Type Definitions - Request Types
# =============================================================================


class GuestRoom(TypedDict):
    """Room configuration with guest counts."""

    adults: int
    children: NotRequired[list[int]]  # Ages of children (0-17)


# =============================================================================
# Type Definitions - Response Types
# =============================================================================


class Region(TypedDict):
    """Region from autocomplete response."""

    id: int
    name: str
    type: str  # "City", "Country", "Airport", etc.
    country_code: str  # ISO 3166-1 alpha-2


class MealData(TypedDict):
    """Meal information for a rate."""

    value: str  # "nomeal", "breakfast", etc.
    has_breakfast: bool
    no_child_meal: NotRequired[bool]


class TaxInfo(TypedDict):
    """Tax information."""

    name: str
    included_by_supplier: bool
    amount: str
    currency_code: str


class TaxData(TypedDict):
    """Tax data container."""

    taxes: list[TaxInfo]


class CancellationPolicy(TypedDict):
    """Cancellation policy period."""

    start_at: str | None
    end_at: str | None
    amount_charge: str
    amount_show: str


class CancellationPenalties(TypedDict):
    """Cancellation penalties information."""

    policies: list[CancellationPolicy]
    free_cancellation_before: NotRequired[str | None]


class PaymentType(TypedDict):
    """Payment option details."""

    type: str  # "now" or "deposit"
    amount: str
    show_amount: str
    currency_code: str
    show_currency_code: str
    by: NotRequired[str]  # "credit_card", etc.
    is_need_credit_card_data: bool
    is_need_cvc: NotRequired[bool]
    tax_data: NotRequired[TaxData]
    cancellation_penalties: NotRequired[CancellationPenalties]


class PaymentOptions(TypedDict):
    """Payment options container."""

    payment_types: list[PaymentType]


class RoomExtension(TypedDict):
    """Extended room characteristics."""

    class_: NotRequired[int]  # Note: 'class' is reserved, use rg_ext["class"]
    quality: int
    sex: NotRequired[int]
    bathroom: int
    bedding: int
    family: NotRequired[int]
    capacity: int
    club: NotRequired[int]
    bedrooms: NotRequired[int]
    balcony: NotRequired[int]
    view: NotRequired[int]
    floor: NotRequired[int]


class HotelRate(TypedDict):
    """Hotel rate/offer information."""

    match_hash: str
    search_hash: str | None
    daily_prices: list[str]
    meal: str
    meal_data: MealData
    payment_options: PaymentOptions
    rg_ext: dict[str, int]  # RoomExtension, but 'class' key is problematic
    room_name: str
    room_name_info: NotRequired[str | None]
    serp_filters: NotRequired[list[str]]
    amenities_data: NotRequired[list[str]]
    allotment: NotRequired[int]
    any_residency: NotRequired[bool]


class Hotel(TypedDict):
    """Hotel in search results."""

    id: str
    hid: int
    rates: list[HotelRate]


class SearchResults(TypedDict):
    """Hotel search results."""

    hotels: list[Hotel]
    total_hotels: int


# =============================================================================
# Type Definitions - Reviews
# =============================================================================


class DetailedReview(TypedDict):
    """Detailed review scores."""

    cleanness: int  # 0-10
    location: int  # 0-10
    price: int  # 0-10
    services: int  # 0-10
    room: int  # 0-10
    meal: int  # 0-10
    wifi: str  # "unspecified", "positive", "negative"
    hygiene: str  # "unspecified", "positive", "negative"


class Review(TypedDict):
    """Hotel review."""

    id: int
    review_plus: str | None
    review_minus: str | None
    created: str  # ISO timestamp
    author: str
    adults: int
    children: int
    room_name: str
    nights: int
    images: list[str] | None
    detailed_review: NotRequired[DetailedReview]
    traveller_type: str  # "solo", "couple", "family", "unspecified"
    trip_type: str  # "leisure", "business"
    rating: float  # 0-10, 0 means no rating


class HotelReviews(TypedDict):
    """Hotel with its reviews."""

    id: str
    hid: int
    reviews: list[Review]


# =============================================================================
# Type Definitions - Hotel Content
# =============================================================================


class ImageInfo(TypedDict):
    """Hotel image information."""

    url: str  # Contains {size} placeholder
    width: NotRequired[int]
    height: NotRequired[int]


class ImageGroup(TypedDict):
    """Group of images by category."""

    group_name: str
    images: list[ImageInfo]


class Amenity(TypedDict):
    """Hotel amenity."""

    name: str
    free: NotRequired[bool]


class AmenityGroup(TypedDict):
    """Group of amenities by category."""

    group_name: str
    amenities: list[Amenity]


class DescriptionParagraph(TypedDict):
    """Description paragraph."""

    title: NotRequired[str]
    paragraphs: list[str]


class RoomAmenity(TypedDict):
    """Room amenity."""

    name: str
    free: NotRequired[bool]


class RoomGroup(TypedDict):
    """Room type information."""

    room_group_id: int
    name: str
    name_struct: NotRequired[dict[str, str]]
    room_amenities: NotRequired[list[RoomAmenity]]
    images: NotRequired[list[ImageInfo]]
    rg_ext: NotRequired[dict[str, int]]


class RegionInfo(TypedDict):
    """Region information."""

    id: int
    name: str
    type: str
    country_code: NotRequired[str]
    iata: NotRequired[str]


class MetapolicyInfo(TypedDict):
    """Policy information."""

    check_in: NotRequired[str]
    check_out: NotRequired[str]
    deposit: NotRequired[str]
    pets: NotRequired[str]
    parking: NotRequired[str]
    shuttle: NotRequired[str]
    children: NotRequired[str]
    meal: NotRequired[str]


class HotelContent(TypedDict):
    """Hotel content information."""

    id: str
    hid: int
    name: str
    address: str
    latitude: float
    longitude: float
    star_rating: int  # 1-5, 0 = unavailable
    kind: str  # Hotel, Resort, Hostel, etc.
    phone: NotRequired[str]
    email: NotRequired[str]
    check_in_time: NotRequired[str]
    check_out_time: NotRequired[str]
    description_struct: NotRequired[list[DescriptionParagraph]]
    amenity_groups: NotRequired[list[AmenityGroup]]
    images_ext: NotRequired[list[ImageGroup]]
    room_groups: NotRequired[list[RoomGroup]]
    region: NotRequired[RegionInfo]
    metapolicy_struct: NotRequired[MetapolicyInfo]
    payment_methods: NotRequired[list[str]]
    front_desk_time_start: NotRequired[str]
    front_desk_time_end: NotRequired[str]


# =============================================================================
# Exceptions
# =============================================================================


class ETGClientError(Exception):
    """Base exception for ETG client errors."""

    pass


class ETGAuthError(ETGClientError):
    """Authentication failed."""

    pass


class ETGAPIError(ETGClientError):
    """API returned an error response."""

    pass


class ETGNetworkError(ETGClientError):
    """Network-related error occurred."""

    pass


# =============================================================================
# Client
# =============================================================================


BASE_URL = "https://api.worldota.net"


class ETGClient:
    """
    ETG B2B API v3 Client (Sync)

    Provides methods to interact with the ETG hotel booking API.
    Uses HTTP Basic Authentication.
    """

    def __init__(self, key_id: str, api_key: str, timeout: float = 30.0) -> None:
        """
        Initialize the ETG client.

        Args:
            key_id: API key ID for authentication
            api_key: API key secret for authentication
            timeout: Request timeout in seconds
        """
        self._auth = httpx.BasicAuth(key_id, api_key)
        self._timeout = httpx.Timeout(timeout)
        self._client = httpx.Client(
            base_url=BASE_URL,
            auth=self._auth,
            timeout=self._timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "ETGClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.close()

    def _request[T](self, endpoint: str, payload: dict[str, object]) -> dict[str, T]:
        """
        Make a POST request to the ETG API.

        Args:
            endpoint: API endpoint path (e.g., "/api/b2b/v3/search/serp/region/")
            payload: Request body as dictionary

        Returns:
            Parsed JSON response

        Raises:
            ETGAuthError: If authentication fails (401/403)
            ETGAPIError: If API returns an error
            ETGNetworkError: If network error occurs
        """
        start_time = time.perf_counter()
        try:
            response = self._client.post(endpoint, json=payload)
        except httpx.TimeoutException:
            elapsed = time.perf_counter() - start_time
            print(f"  [ETG] {endpoint} - TIMEOUT after {elapsed:.2f}s")
            raise ETGNetworkError("Request timed out")
        except httpx.ConnectError as e:
            elapsed = time.perf_counter() - start_time
            print(f"  [ETG] {endpoint} - CONNECTION ERROR after {elapsed:.2f}s")
            raise ETGNetworkError(f"Connection error: {e}")
        except httpx.RequestError as e:
            elapsed = time.perf_counter() - start_time
            print(f"  [ETG] {endpoint} - REQUEST ERROR after {elapsed:.2f}s")
            raise ETGNetworkError(f"Request failed: {e}")

        elapsed = time.perf_counter() - start_time
        print(f"  [ETG] {endpoint} - {response.status_code} in {elapsed:.2f}s")

        # Handle HTTP errors
        if response.status_code == 401:
            raise ETGAuthError("Authentication failed: Invalid credentials")
        if response.status_code == 403:
            raise ETGAuthError("Authentication failed: Access forbidden")
        if response.status_code >= 400:
            raise ETGAPIError(
                f"API error (HTTP {response.status_code}): {response.text[:500]}"
            )

        # Parse JSON response
        try:
            data: dict[str, T] = response.json()
        except ValueError as e:
            raise ETGAPIError(f"Invalid JSON response: {e}")

        # Check for API-level errors
        if data.get("status") != "ok" and data.get("error"):
            error_info = data.get("error", {})
            raise ETGAPIError(f"API error: {error_info}")

        return data

    def suggest_region(self, query: str, language: str = "en") -> list[Region]:
        """
        Search for regions (cities, countries, etc.) by name.

        Args:
            query: Search query (e.g., "Berlin", "Paris")
            language: Response language

        Returns:
            List of matching regions with id, name, type, country_code
        """
        payload: dict[str, str] = {
            "query": query,
            "language": language,
        }

        response = self._request(endpoint="/api/b2b/v3/search/multicomplete/", payload=payload)
        data = response.get("data")
        if data is None or not isinstance(data, dict):
            return []
        regions = data.get("regions")
        if regions is None or not isinstance(regions, list):
            return []
        return regions  # type: ignore[return-value]

    def search_hotels_by_region(
        self,
        region_id: int,
        checkin: str,
        checkout: str,
        residency: str,
        guests: list[GuestRoom] | None = None,
        currency: str | None = None,
        language: str | None = None,
        hotels_limit: int | None = None,
    ) -> SearchResults:
        """
        Search for available hotels in a region.

        Args:
            region_id: Region ID (from suggest_region)
            checkin: Check-in date (YYYY-MM-DD)
            checkout: Check-out date (YYYY-MM-DD)
            residency: Guest citizenship (ISO 3166-1 alpha-2)
            guests: List of room configurations
            currency: Price currency (ISO 4217)
            language: Response language
            hotels_limit: Maximum hotels to return

        Returns:
            Search results containing hotels list and total count
        """
        def _normalize_guests(guest_rooms: list[GuestRoom]) -> list[GuestRoom]:
            normalized: list[GuestRoom] = []
            for room in guest_rooms:
                room_dict = dict(room)
                children = room_dict.get("children")
                if children is not None:
                    normalized_children: list[int] = []
                    for child in children:
                        if isinstance(child, dict):
                            age = child.get("age")
                            if age is not None:
                                normalized_children.append(int(age))
                        else:
                            normalized_children.append(int(child))
                    room_dict["children"] = normalized_children
                normalized.append(room_dict)  # type: ignore[list-item]
            return normalized

        payload: dict[str, object] = {
            "region_id": region_id,
            "checkin": checkin,
            "checkout": checkout,
            "residency": residency,
        }

        if guests:
            payload["guests"] = _normalize_guests(guests)
        if currency:
            payload["currency"] = currency
        if language:
            payload["language"] = language
        if hotels_limit is not None:
            payload["hotels_limit"] = hotels_limit

        response = self._request(endpoint="/api/b2b/v3/search/serp/region/", payload=payload)
        data = response.get("data")
        if data is None or not isinstance(data, dict):
            return {"hotels": [], "total_hotels": 0}
        return data  # type: ignore[return-value]

    def get_hotel_reviews(
        self,
        hids: list[int],
        language: str = "en",
    ) -> list[HotelReviews]:
        """
        Get reviews for hotels by their numeric IDs.

        Args:
            hids: List of hotel IDs (numeric format, max 100 per request)
            language: Language for review content

        Returns:
            List of hotels with their reviews
        """
        payload: dict[str, object] = {
            "hids": hids,
            "language": language,
        }

        response = self._request(
            endpoint="/api/content/v1/hotel_reviews_by_ids/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, list):
            return []
        return data  # type: ignore[return-value]

    def get_hotel_content(
        self,
        hids: list[int],
        language: str = "en",
    ) -> list[HotelContent]:
        """
        Get content (details) for hotels by their numeric IDs.

        Args:
            hids: List of hotel IDs (numeric format, max 100 per request)
            language: Language for content

        Returns:
            List of hotels with their content
        """
        payload: dict[str, object] = {
            "hids": hids,
            "language": language,
        }

        response = self._request(
            endpoint="/api/content/v1/hotel_content_by_ids/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, list):
            return []
        return data  # type: ignore[return-value]


class AsyncETGClient:
    """
    ETG B2B API v3 Client (Async)

    Async version using httpx.AsyncClient.
    """

    def __init__(self, key_id: str, api_key: str, timeout: float = 30.0) -> None:
        self._auth = httpx.BasicAuth(key_id, api_key)
        self._timeout = httpx.Timeout(timeout)
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            auth=self._auth,
            timeout=self._timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncETGClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.close()

    async def _request[T](self, endpoint: str, payload: dict[str, object]) -> dict[str, T]:
        start_time = time.perf_counter()
        try:
            response = await self._client.post(endpoint, json=payload)
        except httpx.TimeoutException:
            elapsed = time.perf_counter() - start_time
            print(f"  [ETG] {endpoint} - TIMEOUT after {elapsed:.2f}s")
            raise ETGNetworkError("Request timed out")
        except httpx.ConnectError as e:
            elapsed = time.perf_counter() - start_time
            print(f"  [ETG] {endpoint} - CONNECTION ERROR after {elapsed:.2f}s")
            raise ETGNetworkError(f"Connection error: {e}")
        except httpx.RequestError as e:
            elapsed = time.perf_counter() - start_time
            print(f"  [ETG] {endpoint} - REQUEST ERROR after {elapsed:.2f}s")
            raise ETGNetworkError(f"Request failed: {e}")

        elapsed = time.perf_counter() - start_time
        print(f"  [ETG] {endpoint} - {response.status_code} in {elapsed:.2f}s")

        if response.status_code == 401:
            raise ETGAuthError("Authentication failed: Invalid credentials")
        if response.status_code == 403:
            raise ETGAuthError("Authentication failed: Access forbidden")
        if response.status_code >= 400:
            raise ETGAPIError(
                f"API error (HTTP {response.status_code}): {response.text[:500]}"
            )

        try:
            data: dict[str, T] = response.json()
        except ValueError as e:
            raise ETGAPIError(f"Invalid JSON response: {e}")

        if data.get("status") != "ok" and data.get("error"):
            error_info = data.get("error", {})
            raise ETGAPIError(f"API error: {error_info}")

        return data

    async def suggest_region(self, query: str, language: str = "en") -> list[Region]:
        payload: dict[str, str] = {
            "query": query,
            "language": language,
        }
        response = await self._request(
            endpoint="/api/b2b/v3/search/multicomplete/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, dict):
            return []
        regions = data.get("regions")
        if regions is None or not isinstance(regions, list):
            return []
        return regions  # type: ignore[return-value]

    async def search_hotels_by_region(
        self,
        region_id: int,
        checkin: str,
        checkout: str,
        residency: str,
        guests: list[GuestRoom] | None = None,
        currency: str | None = None,
        language: str | None = None,
        hotels_limit: int | None = None,
    ) -> SearchResults:
        def _normalize_guests(guest_rooms: list[GuestRoom]) -> list[GuestRoom]:
            normalized: list[GuestRoom] = []
            for room in guest_rooms:
                room_dict = dict(room)
                children = room_dict.get("children")
                if children is not None:
                    normalized_children: list[int] = []
                    for child in children:
                        if isinstance(child, dict):
                            age = child.get("age")
                            if age is not None:
                                normalized_children.append(int(age))
                        else:
                            normalized_children.append(int(child))
                    room_dict["children"] = normalized_children
                normalized.append(room_dict)  # type: ignore[list-item]
            return normalized

        payload: dict[str, object] = {
            "region_id": region_id,
            "checkin": checkin,
            "checkout": checkout,
            "residency": residency,
        }

        if guests:
            payload["guests"] = _normalize_guests(guests)
        if currency:
            payload["currency"] = currency
        if language:
            payload["language"] = language
        if hotels_limit is not None:
            payload["hotels_limit"] = hotels_limit

        response = await self._request(
            endpoint="/api/b2b/v3/search/serp/region/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, dict):
            return {"hotels": [], "total_hotels": 0}
        return data  # type: ignore[return-value]

    async def get_hotel_reviews(
        self,
        hids: list[int],
        language: str = "en",
    ) -> list[HotelReviews]:
        payload: dict[str, object] = {
            "hids": hids,
            "language": language,
        }
        response = await self._request(
            endpoint="/api/content/v1/hotel_reviews_by_ids/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, list):
            return []
        return data  # type: ignore[return-value]

    async def get_hotel_content(
        self,
        hids: list[int],
        language: str = "en",
    ) -> list[HotelContent]:
        payload: dict[str, object] = {
            "hids": hids,
            "language": language,
        }
        response = await self._request(
            endpoint="/api/content/v1/hotel_content_by_ids/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, list):
            return []
        return data  # type: ignore[return-value]
