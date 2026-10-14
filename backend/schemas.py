"""
Pydantic models = the shape of the data going in and out of the API.

FastAPI uses these to validate requests automatically (so a negative budget
or a backwards date range gets rejected before our code even runs) and to
build the docs at http://127.0.0.1:8000/docs.
"""

from datetime import date, timedelta

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

INTEREST_CHOICES = [
    "Nature", "Food", "Culture & Heritage", "Adventure",
    "Nightlife", "Shopping", "Relaxation", "Photography", "Spiritual",
]

# How the traveller wants the budget spent. Fitting the budget is always the
# hard rule - this decides what we try FIRST and what we give up LAST.
#
#   budget    squeeze it. Cheapest transport, hostels, street food.
#   balanced  sensible middle. Fly if it fits, otherwise train.
#   comfort   spend the money. Flights and a decent hotel; only drop down
#             if the budget genuinely will not stretch.
TRAVEL_STYLES = ["budget", "balanced", "comfort"]


class TripRequest(BaseModel):
    """What the frontend sends when you hit 'Plan my trip'."""

    source_city: str = Field(min_length=2, max_length=120, examples=["Dehradun"])
    destination_city: str = Field(min_length=2, max_length=120, examples=["Jaipur"])
    start_date: date
    end_date: date
    travelers: int = Field(ge=1, le=20, default=2)
    # Optional split. When given, travelers = adults + children. Used to work
    # out how many rooms (or hostel beds) the group needs.
    adults: int | None = Field(default=None, ge=1, le=20)
    children: int = Field(default=0, ge=0, le=15)
    budget_inr: int = Field(ge=1000, le=10_000_000, examples=[25000])
    interests: list[str] = Field(default_factory=list)
    travel_style: str = Field(default="balanced", examples=["comfort"])
    notes: str = Field(default="", max_length=500,
                       examples=["No flights before 8am, we are vegetarian"])

    @field_validator("travel_style")
    @classmethod
    def known_style(cls, v: str) -> str:
        v = (v or "balanced").strip().lower()
        if v not in TRAVEL_STYLES:
            raise ValueError(f"travel_style must be one of {TRAVEL_STYLES}")
        return v

    @field_validator("source_city", "destination_city")
    @classmethod
    def clean_city(cls, v: str) -> str:
        return v.strip().title()

    @model_validator(mode="after")
    def check_dates(self):
        if self.adults is not None:
            self.travelers = self.adults + self.children
            if self.travelers > 20:
                raise ValueError("At most 20 travellers")
        if self.start_date < date.today() - timedelta(days=1):
            # (one day of slack for time zones: it may still be "yesterday" for the server)
            raise ValueError("The trip cannot start in the past")
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        if (self.end_date - self.start_date).days > 21:
            raise ValueError("Trips longer than 21 days are not supported yet")
        if self.source_city.lower() == self.destination_city.lower():
            raise ValueError("Source and destination are the same city")
        return self

    @property
    def num_days(self) -> int:
        return max(1, (self.end_date - self.start_date).days + 1)

    @property
    def num_adults(self) -> int:
        return self.adults if self.adults is not None else max(1, self.travelers - self.children)

    @property
    def num_nights(self) -> int:
        return max(1, (self.end_date - self.start_date).days)

    def as_text(self) -> str:
        """A readable one-paragraph version we can paste into an LLM prompt."""
        interests = ", ".join(self.interests) if self.interests else "no specific interests"
        style_text = {
            "budget": "Wants the cheapest possible trip.",
            "balanced": "Wants a sensible balance of cost and comfort.",
            "comfort": "Wants a comfortable trip and is happy to spend the budget on it.",
        }[self.travel_style]

        line = (
            f"Trip from {self.source_city} to {self.destination_city}, "
            f"{self.start_date} to {self.end_date} ({self.num_days} days, {self.num_nights} nights), "
            f"{self.travelers} traveller(s)"
            f"{f' ({self.num_adults} adults, {self.children} children)' if self.children else ''}, "
            f"total budget Rs {self.budget_inr:,}. "
            f"Interests: {interests}. {style_text}"
        )
        if self.notes:
            line += f" Special notes from the traveller: {self.notes}"
        return line


class TripSummary(BaseModel):
    """Short version, used for the 'my trips' list."""
    id: str
    source_city: str
    destination_city: str
    start_date: str
    end_date: str
    travelers: int
    budget_inr: int
    status: str
    fits_budget: bool
    total_cost_inr: int
    created_at: str


class TripDetail(TripSummary):
    """Full version with the actual plan."""
    interests: list[str] = []
    travel_style: str = "balanced"
    notes: str = ""
    plan: dict = {}
    trace: list = []
    replan_rounds: int = 0
    plan_cost_inr: float = 0.0
    plan_ms: float = 0.0
    alerts: list[dict] = []


class TravelerDetails(BaseModel):
    """Collected on the checkout page before the (test mode) payment."""
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: str = Field(min_length=8, max_length=15)
