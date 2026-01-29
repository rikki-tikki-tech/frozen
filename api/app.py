"""FastAPI application factory."""

from typing import Annotated, Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from config import CORS_ORIGINS, ETG_API_KEY, ETG_KEY_ID, ETG_REQUEST_TIMEOUT
from etg import ETGClient, Region

from .schemas import HotelSearchRequest, RegionItem, RegionSuggestResponse
from .search import search_stream


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    etg_client = ETGClient(ETG_KEY_ID, ETG_API_KEY, timeout=ETG_REQUEST_TIMEOUT)

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {"message": "Hello World"}

    @app.get("/regions/suggest")
    async def suggest_regions(
        query: Annotated[str, Query(min_length=1, description="Поисковый запрос")],
        language: Annotated[str, Query(pattern=r"^[a-z]{2}$", description="Код языка")] = "ru",
    ) -> RegionSuggestResponse:
        """Поиск региона по названию."""
        raw_regions: list[Region] = await etg_client.suggest_region(query, language)

        regions = [
            RegionItem(
                id=r["id"],
                name=r["name"],
                type=r["type"],
                country_code=r.get("country_code", ""),
            )
            for r in raw_regions
        ]

        city = next((r for r in regions if r.type == "City"), None)

        return RegionSuggestResponse(
            query=query,
            regions=regions,
            city=city,
        )

    @app.post("/hotels/search/stream")
    async def stream_hotels_search(request: HotelSearchRequest) -> StreamingResponse:
        return StreamingResponse(
            search_stream(request, etg_client),
            media_type="text/event-stream",
        )

    return app
