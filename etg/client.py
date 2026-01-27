"""
ETG (Emerging Travel Group / Ostrovok) B2B API v3 Client

Provides sync and async interfaces to interact with the ETG hotel booking API.
Uses httpx for HTTP requests with Basic Authentication.

API Documentation: https://docs.emergingtravel.com/docs/
"""

import time

import httpx

from .exceptions import ETGAPIError, ETGAuthError, ETGNetworkError
from .types import (
    GuestRoom,
    HotelContent,
    HotelReviews,
    Region,
    SearchResults,
)

BASE_URL = "https://api.worldota.net"


def _normalize_guests(guest_rooms: list[GuestRoom]) -> list[GuestRoom]:
    """Normalize guest room data (handles both dict-style and int children)."""
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


class ETGClient:
    """
    ETG B2B API v3 Client (Sync)

    Uses HTTP Basic Authentication.
    """

    def __init__(self, key_id: str, api_key: str, timeout: float = 30.0) -> None:
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

    def _request(self, endpoint: str, payload: dict[str, object]) -> dict:
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

        if response.status_code == 401:
            raise ETGAuthError("Authentication failed: Invalid credentials")
        if response.status_code == 403:
            raise ETGAuthError("Authentication failed: Access forbidden")
        if response.status_code >= 400:
            raise ETGAPIError(
                f"API error (HTTP {response.status_code}): {response.text[:500]}"
            )

        try:
            data = response.json()
        except ValueError as e:
            raise ETGAPIError(f"Invalid JSON response: {e}")

        if data.get("status") != "ok" and data.get("error"):
            error_info = data.get("error", {})
            raise ETGAPIError(f"API error: {error_info}")

        return data

    def suggest_region(self, query: str, language: str = "en") -> list[Region]:
        """Search for regions (cities, countries, etc.) by name."""
        payload: dict[str, str] = {
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
        """Search for available hotels in a region."""
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

        response = self._request(
            endpoint="/api/b2b/v3/search/serp/region/", payload=payload
        )
        data = response.get("data")
        if data is None or not isinstance(data, dict):
            return {"hotels": [], "total_hotels": 0}
        return data  # type: ignore[return-value]

    def get_hotel_reviews(
        self,
        hids: list[int],
        language: str = "en",
    ) -> list[HotelReviews]:
        """Get reviews for hotels by their numeric IDs."""
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
        """Get content (details) for hotels by their numeric IDs."""
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

    async def _request(
        self, endpoint: str, payload: dict[str, object]
    ) -> dict:
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
            data = response.json()
        except ValueError as e:
            raise ETGAPIError(f"Invalid JSON response: {e}")

        if data.get("status") != "ok" and data.get("error"):
            error_info = data.get("error", {})
            raise ETGAPIError(f"API error: {error_info}")

        return data

    async def suggest_region(self, query: str, language: str = "en") -> list[Region]:
        """Search for regions by name."""
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
        """Search for available hotels in a region."""
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
        """Get reviews for hotels by their numeric IDs."""
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
        """Get content for hotels by their numeric IDs."""
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
