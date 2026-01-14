# ETG Hotel Search

Поиск доступных отелей через ETG (Emerging Travel Group / Ostrovok) B2B API v3.

## Требования

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Установка

```bash
uv sync
```

## Настройка

Скопируйте файл окружения и заполните ключи:

```bash
cp .env.example .env
```

В `.env`:

- `ETG_KEY_ID` и `ETG_API_KEY` для доступа к ETG API
- `GEMINI_API_KEY` для скоринга через LLM

Затем отредактируйте константы в `etg_hotels.ipynb` (основные настройки в первой ячейке):

```python
# Search Parameters
CITY = "Berlin"           # Город (или REGION_ID напрямую)
REGION_ID = None          # Если задан, CITY игнорируется
CHECKIN_DATE = "2026-02-15"
CHECKOUT_DATE = "2026-02-17"
CURRENCY = "EUR"
LANGUAGE = "en"
RESIDENCY = "DE"          # Гражданство гостя (ISO 3166-1 alpha-2)
GUESTS = [{"adults": 2, "children": [2, 4]}]  # Возраст детей
LIMIT = 1000

# User preferences for AI
USER_PREFERENCES = "..."

# Reviews settings
REVIEWS_PER_SEGMENT = 30
REVIEWS_MAX_AGE_YEARS = 5
NEUTRAL_RATING_THRESHOLD = 7.0
NEGATIVE_RATING_THRESHOLD = 5.0

# Filters
MIN_PRICE = 120.0  # None = no minimum
MAX_PRICE = 400.0  # None = no maximum

# Output
ARTIFACTS_DIR = "artifacts"
```
