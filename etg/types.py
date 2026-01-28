"""ETG API type definitions (request and response types)."""

from typing import NotRequired, TypedDict

# =============================================================================
# Request Types
# =============================================================================


class GuestRoom(TypedDict):
    """Room configuration with guest counts."""

    adults: int
    children: NotRequired[list[int]]  # Ages of children (0-17)


class SearchParams(TypedDict, total=False):
    """Optional search parameters for hotel search.

    All fields are optional and will be passed to the API if provided.
    """

    guests: list[GuestRoom]
    currency: str
    language: str
    hotels_limit: int


# =============================================================================
# Response Types — Search
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

    class_: NotRequired[int]
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
    rg_ext: dict[str, int]
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
# Response Types — Reviews
# =============================================================================


class DetailedReview(TypedDict):
    """Detailed review scores."""

    cleanness: int
    location: int
    price: int
    services: int
    room: int
    meal: int
    wifi: str
    hygiene: str


class Review(TypedDict):
    """Hotel review."""

    id: int
    review_plus: str | None
    review_minus: str | None
    created: str
    author: str
    adults: int
    children: int
    room_name: str
    nights: int
    images: list[str] | None
    detailed_review: NotRequired[DetailedReview]
    traveller_type: str
    trip_type: str
    rating: float


class HotelReviews(TypedDict):
    """Hotel with its reviews."""

    id: str
    hid: int
    reviews: list[Review]


# =============================================================================
# Response Types — Hotel Content
# =============================================================================


class ImageInfo(TypedDict):
    """Hotel image information."""

    url: str
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
    star_rating: int
    kind: str
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
