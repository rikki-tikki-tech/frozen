# CLAUDE.md

## Project Overview

FastAPI service for hotel search and AI-powered scoring via ETG B2B API v3 (Ostrovok/Emerging Travel Group). Development is done in Jupyter notebook, shared code is extracted into modules. Both notebook and API use the same modules.

## Commands

```bash
# Install dependencies
uv sync

# Run API server
uv run uvicorn main:app --host 0.0.0.0 --port 8000

# Run with auto-reload (development)
uv run uvicorn main:app --reload

# Verify imports
uv run python -c "from api import create_app; create_app()"

# Linters
uv run ruff check .          # check style and errors
uv run ruff check . --fix    # auto-fix
uv run mypy .                # type checking
```

No tests yet.

## Architecture

```
main.py           → entry point, creates FastAPI app
config.py         → loads .env (ETG_KEY_ID, ETG_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY, SCORING_MODEL)

etg/              → ETG API client
  types.py        → TypedDict: GuestRoom, Hotel, HotelContent, HotelReviews, Review, Region, SearchResults
  client.py       → ETGClient (async), httpx.AsyncClient + Basic Auth
  exceptions.py   → ETGClientError → ETGAuthError, ETGAPIError, ETGNetworkError

services/         → business logic (async, no FastAPI dependency)
  hotels.py       → filter_hotels_by_price, fetch_hotel_content, presort_hotels, process_search_results
  reviews.py      → fetch_reviews, filter_reviews → HotelReviewsFiltered
  scoring.py      → score_hotels (async generator, Gemini/Claude LLM, batched)

api/              → FastAPI layer
  app.py          → create_app() — factory, CORS, routes
  schemas.py      → HotelSearchRequest, RegionItem, RegionSuggestResponse (Pydantic BaseModel)
  events.py       → SSE events: EventType enum, HotelSearchStartEvent, HotelsFoundEvent, DoneEvent, etc.
  search.py       → search_stream() — async generator, main search pipeline

utils/            → utility functions
  sse.py          → sse_event() — SSE serialization

etg_hotels.ipynb  → Jupyter notebook for research, uses the same modules
```

## Key Decisions

- **Async only** — all code is async, no sync versions. Notebook uses `await` and `async for`.
- **TypedDict over dataclass/Pydantic for ETG types** — data comes as dict from JSON, TypedDict provides typing without conversion overhead.
- **SSE streaming** — search results are delivered progressively via Server-Sent Events, client sees real-time progress.
- **EventType enum** — all event types in a single enum, events contain only structured data without formatted text.
- **Pre-scoring before LLM** — fast sorting by stars/reviews, only top-100 go to LLM.
- **Single LLM scoring request** — one request returns top 10 scored hotels with summary.
- **LLM selection via config** — `SCORING_MODEL` in .env: `gemini-3-flash-preview` or `claude-haiku-4-5`.

## Code Style

- Python 3.13+, uses union types (`str | None`), generic syntax.
- Pydantic v2 for API models, TypedDict for internal data.
- Code comments in English.
- Package manager — `uv`, lock file `uv.lock`.
- Environment variables via `python-dotenv`, configuration in `config.py`.

## Git Commits

Use **Conventional Commits**, short messages in English:

```
feat: add hotel scoring endpoint
fix: handle empty reviews array
refactor: extract presort logic
docs: update API examples
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.

Rules:
- Keep messages short (50 chars max for subject)
- Do NOT add `Co-Authored-By` footer
- Do NOT commit automatically — always ask user first: "Commit: `<message>`?"

## Linters

Project uses **Ruff** and **Mypy** in strict mode.

### Ruff

Configuration in `pyproject.toml`:
- `select = ["ALL"]` — all rules enabled
- Exceptions (minimal):
  - Formatter conflicts (W191, E111, COM812, ISC001...)
  - Conflicting docstring rules (D203 vs D211, D212 vs D213)
  - Cyrillic in strings (RUF001, RUF002, RUF003)

Key enforced rules:
- **Docstrings** — Google style for all public modules, classes, and functions
- **Typing** — type annotations everywhere, avoid `Any` where possible
- **Exceptions** — concrete classes with messages, no string literals in raise
- **Constants** — magic numbers extracted to named constants
- **Complexity** — functions up to 10 branches, up to 50 statements

### Mypy

Configuration in `pyproject.toml`:
- `strict = true` — strict mode
- `warn_unreachable = true` — warnings for unreachable code
- `no_implicit_reexport = true` — explicit re-export in `__init__.py`

Practices:
- `typing.cast()` for API responses (JSON → TypedDict)
- `Self` for `__aenter__` methods
- `Annotated` for FastAPI parameters with validation

## External APIs

- **ETG API** (`https://api.worldota.net`) — hotel search, content, reviews. Basic Auth.
- **Google Gemini** (`gemini-3-flash-preview`) — LLM scoring. Temperature 0.2, thinking LOW.
- **Anthropic Claude** (`claude-haiku-4-5`) — alternative LLM for scoring. Temperature 0.2.
