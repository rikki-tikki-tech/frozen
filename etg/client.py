"""ETG (Emerging Travel Group / Ostrovok) B2B API v3 Client.

Provides sync and async interfaces to interact with the ETG hotel booking API.
Uses httpx for HTTP requests with Basic Authentication.

API Documentation: https://docs.emergingtravel.com/docs/
"""

import logging
import time
from typing import Any, Self, cast

import httpx

from .exceptions import (
    ETGAPIHttpError,
    ETGAPIInvalidJsonError,
    ETGAPIResponseError,
    ETGAuthForbiddenError,
    ETGAuthInvalidCredentialsError,
    ETGConnectionError,
    ETGRequestError,
    ETGTimeoutError,
)
from .types import (
    GuestRoom,
    HotelContent,
    HotelReviews,
    Region,
    SearchParams,
    SearchResults,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.worldota.net"

HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_BAD_REQUEST = 400


def _normalize_guests(guest_rooms: list[GuestRoom]) -> list[dict[str, Any]]:
    """Normalize guest room data.

    Handles both dict-style and int children formats from the API.

    Args:
        guest_rooms: List of guest room configurations.

    Returns:
        Normalized list of guest room dictionaries.
    """
    normalized: list[dict[str, Any]] = []
    for room in guest_rooms:
        room_dict: dict[str, Any] = dict(room)
        children = room_dict.get("children")
        if children is not None and isinstance(children, list):
            normalized_children: list[int] = []
            for child in children:
                if isinstance(child, dict):
                    age = child.get("age")
                    if age is not None:
                        normalized_children.append(int(age))
                else:
                    normalized_children.append(int(child))
            room_dict["children"] = normalized_children
        normalized.append(room_dict)
    return normalized


class ETGClient:
    """ETG B2B API v3 Client (Sync).

    Uses HTTP Basic Authentication for API requests.

    Args:
        key_id: API key ID for authentication.
        api_key: API secret key for authentication.
        timeout: Request timeout in seconds.
    """

    def __init__(self, key_id: str, api_key: str, *, timeout: float = 30.0) -> None:
        """Initialize the ETG client with credentials."""
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
        """Close the HTTP client connection."""
        self._client.close()

    def __enter__(self) -> Self:
        """Enter context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit context manager and close connection."""
        self.close()

    def _request(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Make a POST request to the ETG API.

        Args:
            endpoint: API endpoint path.
            payload: JSON payload to send.

        Returns:
            Parsed JSON response data.

        Raises:
            ETGTimeoutError: If the request times out.
            ETGConnectionError: If connection fails.
            ETGRequestError: If the request fails.
            ETGAuthInvalidCredentialsError: If authentication fails (401).
            ETGAuthForbiddenError: If access is forbidden (403).
            ETGAPIHttpError: If API returns an error status code.
            ETGAPIInvalidJsonError: If response is not valid JSON.
            ETGAPIResponseError: If API returns an error in response body.
        """
        start_time = time.perf_counter()
        try:
            response = self._client.post(endpoint, json=payload)
        except httpx.TimeoutException as e:
            elapsed = time.perf_counter() - start_time
            logger.warning("[ETG] %s - TIMEOUT after %.2fs", endpoint, elapsed)
            raise ETGTimeoutError from e
        except httpx.ConnectError as e:
            elapsed = time.perf_counter() - start_time
            logger.warning("[ETG] %s - CONNECTION ERROR after %.2fs", endpoint, elapsed)
            raise ETGConnectionError(e) from e
        except httpx.RequestError as e:
            elapsed = time.perf_counter() - start_time
            logger.warning("[ETG] %s - REQUEST ERROR after %.2fs", endpoint, elapsed)
            raise ETGRequestError(e) from e

        elapsed = time.perf_counter() - start_time
        logger.debug("[ETG] %s - %d in %.2fs", endpoint, response.status_code, elapsed)

        if response.status_code == HTTP_UNAUTHORIZED:
            raise ETGAuthInvalidCredentialsError
        if response.status_code == HTTP_FORBIDDEN:
            raise ETGAuthForbiddenError
        if response.status_code >= HTTP_BAD_REQUEST:
            raise ETGAPIHttpError(response.status_code, response.text)

        try:
            data: dict[str, Any] = response.json()
        except ValueError as e:
            raise ETGAPIInvalidJsonError(e) from e

        if data.get("status") != "ok" and data.get("error"):
            error_info = data.get("error", {})
            raise ETGAPIResponseError(error_info)

        return data

    def suggest_region(self, query: str, language: str = "en") -> list[Region]:
        """Search for regions (cities, countries, etc.) by name.

        Args:
            query: Search query string.
            language: Response language code (ISO 639-1).

        Returns:
            List of matching regions.
        """
        payload: dict[str, Any] = {
            "query": query,
            "language": language,
        }
        response = self._request(
            endpoint="/api/b2b/v3/search/multicomplete/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, dict):
            return []
        regions = data.get("regions")
        if regions is None or not isinstance(regions, list):
            return []
        return cast("list[Region]", regions)

    def search_hotels_by_region(
        self,
        region_id: int,
        checkin: str,
        checkout: str,
        residency: str,
        params: SearchParams | None = None,
    ) -> SearchResults:
        """Search for available hotels in a region.

        Args:
            region_id: Region identifier.
            checkin: Check-in date (YYYY-MM-DD).
            checkout: Check-out date (YYYY-MM-DD).
            residency: Guest residency country code.
            params: Optional search parameters (guests, currency, language, hotels_limit).

        Returns:
            Search results with hotels and total count.
        """
        payload: dict[str, Any] = {
            "region_id": region_id,
            "checkin": checkin,
            "checkout": checkout,
            "residency": residency,
        }

        if params:
            guests = params.get("guests")
            if guests:
                payload["guests"] = _normalize_guests(guests)
            currency = params.get("currency")
            if currency:
                payload["currency"] = currency
            language = params.get("language")
            if language:
                payload["language"] = language
            hotels_limit = params.get("hotels_limit")
            if hotels_limit is not None:
                payload["hotels_limit"] = hotels_limit

        response = self._request(
            endpoint="/api/b2b/v3/search/serp/region/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, dict):
            return {"hotels": [], "total_hotels": 0}
        return cast("SearchResults", data)

    def get_hotel_reviews(
        self,
        hids: list[int],
        language: str = "en",
    ) -> list[HotelReviews]:
        """Get reviews for hotels by their numeric IDs.

        Args:
            hids: List of hotel numeric IDs.
            language: Review language code.

        Returns:
            List of hotel reviews data.
        """
        payload: dict[str, Any] = {
            "hids": hids,
            "language": language,
        }
        response = self._request(
            endpoint="/api/content/v1/hotel_reviews_by_ids/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, list):
            return []
        return cast("list[HotelReviews]", data)

    def get_hotel_content(
        self,
        hids: list[int],
        language: str = "en",
    ) -> list[HotelContent]:
        """Get content (details) for hotels by their numeric IDs.

        Args:
            hids: List of hotel numeric IDs.
            language: Content language code.

        Returns:
            List of hotel content data.
        """
        payload: dict[str, Any] = {
            "hids": hids,
            "language": language,
        }
        response = self._request(
            endpoint="/api/content/v1/hotel_content_by_ids/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, list):
            return []
        return cast("list[HotelContent]", data)


class AsyncETGClient:
    """ETG B2B API v3 Client (Async).

    Async version using httpx.AsyncClient.

    Args:
        key_id: API key ID for authentication.
        api_key: API secret key for authentication.
        timeout: Request timeout in seconds.
    """

    def __init__(self, key_id: str, api_key: str, *, timeout: float = 30.0) -> None:
        """Initialize the async ETG client with credentials."""
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
        """Close the async HTTP client connection."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        """Enter async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context manager and close connection."""
        await self.close()

    async def _request(
        self, endpoint: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Make an async POST request to the ETG API.

        Args:
            endpoint: API endpoint path.
            payload: JSON payload to send.

        Returns:
            Parsed JSON response data.

        Raises:
            ETGTimeoutError: If the request times out.
            ETGConnectionError: If connection fails.
            ETGRequestError: If the request fails.
            ETGAuthInvalidCredentialsError: If authentication fails (401).
            ETGAuthForbiddenError: If access is forbidden (403).
            ETGAPIHttpError: If API returns an error status code.
            ETGAPIInvalidJsonError: If response is not valid JSON.
            ETGAPIResponseError: If API returns an error in response body.
        """
        start_time = time.perf_counter()
        try:
            response = await self._client.post(endpoint, json=payload)
        except httpx.TimeoutException as e:
            elapsed = time.perf_counter() - start_time
            logger.warning("[ETG] %s - TIMEOUT after %.2fs", endpoint, elapsed)
            raise ETGTimeoutError from e
        except httpx.ConnectError as e:
            elapsed = time.perf_counter() - start_time
            logger.warning("[ETG] %s - CONNECTION ERROR after %.2fs", endpoint, elapsed)
            raise ETGConnectionError(e) from e
        except httpx.RequestError as e:
            elapsed = time.perf_counter() - start_time
            logger.warning("[ETG] %s - REQUEST ERROR after %.2fs", endpoint, elapsed)
            raise ETGRequestError(e) from e

        elapsed = time.perf_counter() - start_time
        logger.debug("[ETG] %s - %d in %.2fs", endpoint, response.status_code, elapsed)

        if response.status_code == HTTP_UNAUTHORIZED:
            raise ETGAuthInvalidCredentialsError
        if response.status_code == HTTP_FORBIDDEN:
            raise ETGAuthForbiddenError
        if response.status_code >= HTTP_BAD_REQUEST:
            raise ETGAPIHttpError(response.status_code, response.text)

        try:
            data: dict[str, Any] = response.json()
        except ValueError as e:
            raise ETGAPIInvalidJsonError(e) from e

        if data.get("status") != "ok" and data.get("error"):
            error_info = data.get("error", {})
            raise ETGAPIResponseError(error_info)

        return data

    async def suggest_region(self, query: str, language: str = "en") -> list[Region]:
        """Search for regions by name.

        Args:
            query: Search query string.
            language: Response language code (ISO 639-1).

        Returns:
            List of matching regions.
        """
        payload: dict[str, Any] = {
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
        return cast("list[Region]", regions)

    async def search_hotels_by_region(
        self,
        region_id: int,
        checkin: str,
        checkout: str,
        residency: str,
        params: SearchParams | None = None,
    ) -> SearchResults:
        """Search for available hotels in a region.

        Args:
            region_id: Region identifier.
            checkin: Check-in date (YYYY-MM-DD).
            checkout: Check-out date (YYYY-MM-DD).
            residency: Guest residency country code.
            params: Optional search parameters (guests, currency, language, hotels_limit).

        Returns:
            Search results with hotels and total count.
        """
        payload: dict[str, Any] = {
            "region_id": region_id,
            "checkin": checkin,
            "checkout": checkout,
            "residency": residency,
        }

        if params:
            guests = params.get("guests")
            if guests:
                payload["guests"] = _normalize_guests(guests)
            currency = params.get("currency")
            if currency:
                payload["currency"] = currency
            language = params.get("language")
            if language:
                payload["language"] = language
            hotels_limit = params.get("hotels_limit")
            if hotels_limit is not None:
                payload["hotels_limit"] = hotels_limit

        response = await self._request(
            endpoint="/api/b2b/v3/search/serp/region/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, dict):
            return {"hotels": [], "total_hotels": 0}
        return cast("SearchResults", data)

    async def get_hotel_reviews(
        self,
        hids: list[int],
        language: str = "en",
    ) -> list[HotelReviews]:
        """Get reviews for hotels by their numeric IDs.

        Args:
            hids: List of hotel numeric IDs.
            language: Review language code.

        Returns:
            List of hotel reviews data.
        """
        payload: dict[str, Any] = {
            "hids": hids,
            "language": language,
        }
        response = await self._request(
            endpoint="/api/content/v1/hotel_reviews_by_ids/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, list):
            return []
        return cast("list[HotelReviews]", data)

    async def get_hotel_content(
        self,
        hids: list[int],
        language: str = "en",
    ) -> list[HotelContent]:
        """Get content for hotels by their numeric IDs.

        Args:
            hids: List of hotel numeric IDs.
            language: Content language code.

        Returns:
            List of hotel content data.
        """
        payload: dict[str, Any] = {
            "hids": hids,
            "language": language,
        }
        response = await self._request(
            endpoint="/api/content/v1/hotel_content_by_ids/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, list):
            return []
        return cast("list[HotelContent]", data)
