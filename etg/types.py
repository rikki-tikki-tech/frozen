"""ETG API type definitions (request and response types)."""

from typing import Literal, NotRequired, TypedDict

# =============================================================================
# Common Types
# =============================================================================

HotelKind = Literal[
    "Hotel",
    "Apart-hotel",
    "Apartment",
    "Hostel",
    "BNB",
    "Guesthouse",
    "Mini-hotel",
    "Boutique_and_Design",
    "Resort",
    "Sanatorium",
    "Villas_and_Bungalows",
    "Cottages_and_Houses",
    "Castle",
    "Farm",
    "Camping",
    "Glamping",
    "Unspecified",
]
"""Hotel property type."""


# =============================================================================
# Request Types
# =============================================================================


class GuestRoom(TypedDict):
    """Room configuration with guest counts."""

    adults: int
    children: NotRequired[list[int]]  # Ages of children (0-17)


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


class NoShowPenalty(TypedDict):
    """No-show penalty information."""

    amount: str
    currency_code: str
    from_time: str


class RoomDataTrans(TypedDict):
    """Translated room data."""

    bathroom: str | None
    bedding_type: str | None
    main_name: str
    main_room_type: str
    misc_room_type: str | None


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
    room_name_info: str | None
    room_data_trans: RoomDataTrans
    serp_filters: NotRequired[list[str]]
    amenities_data: NotRequired[list[str]]
    allotment: NotRequired[int]
    any_residency: NotRequired[bool]
    deposit: NotRequired[str | None]
    is_package: NotRequired[bool]
    legal_info: NotRequired[str | None]
    no_show: NotRequired[NoShowPenalty | None]


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


class ImageExt(TypedDict):
    """Extended image information with category."""

    category_slug: str
    url: str


class AmenityGroup(TypedDict):
    """Group of amenities by category."""

    group_name: str
    amenities: list[str]
    non_free_amenities: list[str] | None


class DescriptionParagraph(TypedDict):
    """Description paragraph."""

    title: NotRequired[str]
    paragraphs: list[str]


class RoomNameStruct(TypedDict):
    """Room name structure."""

    bathroom: str
    bedding_type: str
    main_name: str


class RoomGroup(TypedDict):
    """Room type information."""

    room_group_id: int
    name: str
    name_struct: NotRequired[RoomNameStruct]
    room_amenities: list[str] | None
    images: list[str] | None
    images_ext: list[ImageExt]
    rg_ext: NotRequired[dict[str, int]]


class RegionInfo(TypedDict):
    """Region information."""

    id: int
    name: str
    type: str
    country_code: NotRequired[str]
    iata: NotRequired[str]


class CheckInCheckOutPolicy(TypedDict):
    """Check-in/check-out policy."""

    check_in_check_out_type: str
    currency: str
    inclusion: str
    price: str


class ExtraBedPolicy(TypedDict):
    """Extra bed policy."""

    amount: int
    currency: str
    inclusion: str
    price: str
    price_unit: str


class ParkingPolicy(TypedDict):
    """Parking policy."""

    currency: str
    inclusion: str
    price: str
    price_unit: str
    territory_type: str


class PetsPolicy(TypedDict):
    """Pets policy."""

    currency: str
    inclusion: str
    pets_type: str
    price: str
    price_unit: str


class MealPolicy(TypedDict):
    """Meal policy."""

    currency: str
    inclusion: str
    meal_type: str
    price: str


class NoShowPolicy(TypedDict):
    """No-show policy."""

    availability: str
    day_period: str
    time: str


class VisaPolicy(TypedDict):
    """Visa policy."""

    visa_support: str


class AddFeePolicy(TypedDict):
    """Additional fee policy."""

    currency: str
    fee_type: str
    price: str
    price_unit: str


class MetapolicyStruct(TypedDict):
    """Metapolicy structure."""

    add_fee: list[AddFeePolicy]
    check_in_check_out: list[CheckInCheckOutPolicy]
    children: list[dict[str, str]]
    children_meal: list[dict[str, str]]
    cot: list[dict[str, str]]
    deposit: list[dict[str, str]]
    extra_bed: list[ExtraBedPolicy]
    internet: list[dict[str, str]]
    meal: list[MealPolicy]
    no_show: NoShowPolicy
    parking: list[ParkingPolicy]
    pets: list[PetsPolicy]
    shuttle: list[dict[str, str]]
    visa: VisaPolicy


class PolicyParagraph(TypedDict):
    """Policy paragraph."""

    title: str
    paragraphs: list[str]


class ElectricityFacts(TypedDict):
    """Electricity facts."""

    frequency: list[int]
    sockets: list[str]
    voltage: list[int]


class RegisterFacts(TypedDict):
    """Registration facts."""

    address: str
    email: str
    fsa_kind: str
    fsa_name: str
    kind: str
    link: str
    name: str
    phone: str
    record: str
    rooms: list["RegisterRoom"]
    status: str
    status_end_date: str


class RegisterRoom(TypedDict):
    """Registration room category information."""

    rooms_count: int
    category_type: str


class HotelFacts(TypedDict):
    """Hotel facts."""

    electricity: ElectricityFacts
    floors_number: int
    kind: HotelKind
    register: RegisterFacts | None
    rooms_number: int
    star_rating: int
    type: str
    year_built: int
    year_renovated: int


class KeysPickup(TypedDict):
    """Keys pickup information."""

    apartment_extra_information: str | None
    apartment_office_address: str | None
    email: str | None
    is_contactless: bool | None
    phone: str | None
    type: str | None


class StarCertificate(TypedDict):
    """Star certificate information."""

    certificate_id: str | None
    valid_to: str | None


class HotelContent(TypedDict):
    """Hotel content information."""

    id: str
    hid: int
    name: str
    address: str
    latitude: float
    longitude: float
    star_rating: int
    kind: HotelKind
    deleted: NotRequired[bool]
    is_closed: NotRequired[bool]
    is_gender_specification_required: NotRequired[bool]
    phone: NotRequired[str | None]
    email: NotRequired[str | None]
    postal_code: NotRequired[str | None]
    hotel_chain: NotRequired[str | None]
    check_in_time: NotRequired[str | None]
    check_out_time: NotRequired[str | None]
    front_desk_time_start: NotRequired[str | None]
    front_desk_time_end: NotRequired[str | None]
    description_struct: NotRequired[list[DescriptionParagraph]]
    policy_struct: NotRequired[list[PolicyParagraph]]
    amenity_groups: NotRequired[list[AmenityGroup]]
    images_ext: NotRequired[list[ImageExt]]
    room_groups: NotRequired[list[RoomGroup]]
    region: NotRequired[RegionInfo]
    metapolicy_struct: NotRequired[MetapolicyStruct | None]
    metapolicy_extra_info: NotRequired[str | None]
    payment_methods: NotRequired[list[str] | None]
    facts: NotRequired[HotelFacts | None]
    keys_pickup: NotRequired[KeysPickup | None]
    star_certificate: NotRequired[StarCertificate | None]
    serp_filters: NotRequired[list[str] | None]
