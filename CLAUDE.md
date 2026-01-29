# CLAUDE.md

## Что это за проект

FastAPI-демон для поиска и AI-скоринга отелей через ETG B2B API v3 (Ostrovok/Emerging Travel Group). Разработка ведётся в Jupyter notebook, общий код вынесен в модули. Notebook и API используют одни и те же модули.

## Команды

```bash
# Установка зависимостей
uv sync

# Запуск API-сервера
uv run uvicorn main:app --host 0.0.0.0 --port 8000

# Запуск с авто-перезагрузкой (разработка)
uv run uvicorn main:app --reload

# Проверка импортов
uv run python -c "from api import create_app; create_app()"

# Линтеры
uv run ruff check .          # проверка стиля и ошибок
uv run ruff check . --fix    # автоисправление
uv run mypy .                # проверка типов
```

Тестов пока нет.

## Архитектура

```
main.py           → точка входа, создаёт FastAPI app
config.py         → загружает .env (ETG_KEY_ID, ETG_API_KEY, GEMINI_API_KEY, CORS_ORIGINS)

etg/              → ETG API клиент
  types.py        → TypedDict: GuestRoom, Hotel, HotelContent, HotelReviews, Review, Region, SearchResults
  client.py       → ETGClient (sync) и AsyncETGClient (async), httpx + Basic Auth
  exceptions.py   → ETGClientError → ETGAuthError, ETGAPIError, ETGNetworkError

services/         → бизнес-логика (без зависимости от FastAPI)
  hotels.py       → filter_hotels_by_price, fetch_hotel_content[_async], presort_hotels, get_ostrovok_url
  reviews.py      → fetch_reviews[_async], filter_reviews → HotelReviewsFiltered
  scoring.py      → score_hotels (async generator, Gemini LLM, батчами)

api/              → FastAPI-слой
  app.py          → create_app() — фабрика, CORS, роуты
  schemas.py      → HotelSearchRequest, RegionItem, RegionSuggestResponse (Pydantic BaseModel)
  events.py       → SSE-события: StatusEvent, ScoringStartEvent, DoneEvent и др.
  search.py       → search_stream() — async generator, основной пайплайн поиска

utils/            → вспомогательные функции
  formatting.py   → format_dates, format_guests (русский текст)
  sse.py          → sse_event() — сериализация в SSE формат

etg_hotels.ipynb  → Jupyter notebook для исследования, использует те же модули
```

## Ключевые решения

- **TypedDict вместо dataclass/Pydantic для ETG типов** — данные приходят как dict из JSON, TypedDict даёт типизацию без накладных расходов на конвертацию.
- **Sync + Async клиенты** — sync для notebook, async для API-сервера.
- **SSE-стриминг** — результаты поиска отдаются постепенно через Server-Sent Events, клиент видит прогресс в реальном времени.
- **Пре-скоринг перед LLM** — быстрая сортировка по звёздам/отзывам, в LLM уходят только топ-100.
- **Батчевый LLM-скоринг** — по 25 отелей за запрос к Gemini, с retry логикой.
- **Общие модули для notebook и API** — изменения в `services/`, `etg/` сразу работают и в notebook, и в сервере.

## Стиль кода

- Python 3.13+, используются union types (`str | None`), generic syntax.
- Pydantic v2 для API-моделей, TypedDict для внутренних данных.
- Русский язык в UI-сообщениях и комментариях к API.
- Менеджер пакетов — `uv`, lock-файл `uv.lock`.
- Переменные окружения через `python-dotenv`, конфигурация в `config.py`.

## Линтеры

Проект использует **Ruff** и **Mypy** в строгом режиме.

### Ruff

Конфигурация в `pyproject.toml`:
- `select = ["ALL"]` — включены все правила
- Исключения (минимум):
  - Конфликты с форматтером (W191, E111, COM812, ISC001...)
  - Конфликтующие правила docstring (D203 vs D211, D212 vs D213)
  - Кириллица в строках (RUF001, RUF002, RUF003)

Ключевые соблюдаемые правила:
- **Docstrings** — Google-стиль для всех публичных модулей, классов и функций
- **Типизация** — аннотации типов везде, без `Any` где возможно
- **Исключения** — конкретные классы с сообщениями, без строковых литералов в raise
- **Константы** — магические числа вынесены в именованные константы
- **Сложность** — функции до 10 branches, до 50 statements

### Mypy

Конфигурация в `pyproject.toml`:
- `strict = true` — строгий режим
- `warn_unreachable = true` — предупреждения о недостижимом коде
- `no_implicit_reexport = true` — явный реэкспорт в `__init__.py`

Практики:
- `typing.cast()` для API-ответов (JSON → TypedDict)
- `Self` для `__enter__`/`__aenter__` методов
- `Annotated` для FastAPI параметров с валидацией

## Внешние API

- **ETG API** (`https://api.worldota.net`) — поиск отелей, контент, отзывы. Basic Auth.
- **Google Gemini** (`gemini-3-flash-preview`) — LLM-скоринг. Temperature 0.2, thinking LOW.
