# ETG Hotel Search

Поиск и AI-скоринг отелей через ETG (Emerging Travel Group / Ostrovok) B2B API v3.

Два режима работы:
- **Jupyter notebook** — интерактивная разработка и анализ
- **API-демон** — FastAPI сервер со стримингом результатов через SSE

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

Переменные в `.env`:

| Переменная | Описание |
|---|---|
| `ETG_KEY_ID` | ID ключа ETG API |
| `ETG_API_KEY` | Секретный ключ ETG API |
| `GEMINI_API_KEY` | API-ключ Google Gemini для LLM-скоринга |

## Jupyter notebook

Основной режим разработки. Настройте параметры поиска в первой ячейке `etg_hotels.ipynb`:

```python
CITY = "Москва"
CHECKIN_DATE = "2026-02-20"
CHECKOUT_DATE = "2026-02-23"
CURRENCY = "EUR"
LANGUAGE = "ru"
RESIDENCY = "RU"
GUESTS = [{"adults": 2, "children": []}]
USER_PREFERENCES = "Большая кровать, рядом с центром"
MIN_PRICE = 0.0
MAX_PRICE = 150.0
```

Notebook импортирует всё из общих модулей (`etg`, `services`, `utils`), поэтому изменения в коде сразу доступны и в API, и в notebook.

## API-сервер

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

### Эндпоинты

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/regions/suggest?query=Berlin&language=en` | Поиск региона по названию |
| `POST` | `/hotels/search/stream` | Поиск отелей (SSE-стриминг) |

### Пример запроса поиска

```bash
curl -X POST http://localhost:8000/hotels/search/stream \
  -H "Content-Type: application/json" \
  -d '{
    "region_id": 2395,
    "city": "Москва",
    "checkin": "2026-02-20",
    "checkout": "2026-02-23",
    "guests": [{"adults": 2, "children": []}],
    "residency": "ru",
    "user_preferences": "Большая кровать, рядом с метро"
  }'
```

## Пайплайн поиска

1. Поиск доступных отелей через ETG API по региону и датам
2. Фильтрация по цене за ночь
3. Получение контента (описание, удобства, фото)
4. Получение отзывов на нескольких языках
5. Фильтрация отзывов по давности (5 лет) и сегментация (позитивные/нейтральные/негативные)
6. Пре-скоринг: звёзды + соотношение отзывов + количество → топ-100
7. LLM-скоринг через Gemini по предпочтениям пользователя (батчами по 25)
8. Финальная сортировка и формирование ссылок на Островок

## Структура проекта

```
main.py              — точка входа (uvicorn)
config.py            — конфигурация из переменных окружения
etg_hotels.ipynb     — Jupyter notebook для исследования и отладки

etg/                 — ETG API клиент
  types.py           — типы данных (GuestRoom, Hotel, HotelContent, Review...)
  client.py          — синхронный и асинхронный HTTP-клиенты
  exceptions.py      — иерархия ошибок API

services/            — бизнес-логика
  hotels.py          — фильтрация по цене, пре-скоринг, URL Островка
  reviews.py         — получение и фильтрация отзывов по дате/рейтингу
  scoring.py         — LLM-скоринг отелей через Google Gemini

api/                 — FastAPI слой
  app.py             — фабрика приложения, CORS, роуты
  schemas.py         — Pydantic модели запросов и ответов
  events.py          — модели SSE-событий
  search.py          — пайплайн стримингового поиска

utils/               — утилиты
  formatting.py      — форматирование дат и гостей
  sse.py             — сериализация SSE-событий
```
