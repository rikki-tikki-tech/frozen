"""API request and response schemas."""

from datetime import date

from pydantic import BaseModel, Field, model_validator

from etg import GuestRoom


class RegionItem(BaseModel):
    """Регион из результатов поиска."""

    id: int = Field(description="ID региона")
    name: str = Field(description="Название региона")
    type: str = Field(description="Тип региона (City, Country, Airport и т.д.)")
    country_code: str = Field(description="Код страны (ISO 3166-1 alpha-2)")


class RegionSuggestResponse(BaseModel):
    """Ответ на запрос поиска региона."""

    query: str = Field(description="Исходный поисковый запрос")
    regions: list[RegionItem] = Field(description="Все найденные регионы")
    city: RegionItem | None = Field(description="Первый найденный город (или None)")


class HotelSearchRequest(BaseModel):
    """Запрос на поиск отелей."""

    region_id: int = Field(gt=0, description="ID региона поиска")
    checkin: date = Field(description="Дата заезда")
    checkout: date = Field(description="Дата выезда")
    guests: list[GuestRoom] = Field(min_length=1, description="Количество гостей")
    residency: str = Field(
        pattern=r"^[a-z]{2}$",
        description="Код страны проживания (ISO 3166-1 alpha-2)",
    )
    currency: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{3}$",
        description="Код валюты (ISO 4217)",
    )
    language: str | None = Field(
        default=None,
        pattern=r"^[a-z]{2}$",
        description="Код языка (ISO 639-1)",
    )
    min_price_per_night: float | None = Field(
        default=None, gt=0, description="Минимальная цена за ночь"
    )
    max_price_per_night: float | None = Field(
        default=None, gt=0, description="Максимальная цена за ночь"
    )
    user_preferences: str | None = Field(
        default=None, description="Предпочтения пользователя для AI-скоринга"
    )
    top_hotels: int = Field(
        default=10, ge=1, le=12, description="Количество отелей в результате (макс. 12)"
    )

    @model_validator(mode="after")
    def validate_checkout_after_checkin(self) -> "HotelSearchRequest":
        """Validate that checkout date is after checkin date."""
        if self.checkout <= self.checkin:
            msg = "Дата выезда должна быть позже даты заезда"
            raise ValueError(msg)
        if (
            self.min_price_per_night is not None
            and self.max_price_per_night is not None
            and self.min_price_per_night > self.max_price_per_night
        ):
            msg = "Минимальная цена за ночь не может быть больше максимальной цены"
            raise ValueError(msg)
        return self
