from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SafetyVerdict(Enum):
    OK = "ok"
    ESCALATE_YTP = "escalate_ytp"
    ESCALATE_IDENTITY = "escalate_identity"
    ESCALATE_INJECTION = "escalate_injection"
    ESCALATE_AMBIGUOUS = "escalate_ambiguous"


class Passenger(BaseModel):
    passenger_id: str
    given_name: str
    surname: str
    passenger_type: str
    age: int
    assistance: str | None = None
    cabin_booked: str = "ECONOMY"
    cabin_flown: str | None = None


class Segment(BaseModel):
    segment_id: str
    flight_no: str
    date: str
    origin: str
    destination: str
    segment_fare_gbp: float = 0.0
    cabin: str = "ECONOMY"
    is_affected: bool = False


class Booking(BaseModel):
    booking_ref: str
    customer_id: str
    contact_email: str
    contact_phone: str = ""
    tier: str = "NONE"
    total_paid_gbp: float = 0.0
    special_requests: str = ""
    passengers: list[Passenger] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    final_destination: str = ""
    disruption: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Booking:
        return cls(
            booking_ref=d["booking_ref"],
            customer_id=d["customer_id"],
            contact_email=d["contact_email"],
            contact_phone=d.get("contact_phone", ""),
            tier=d.get("tier", "NONE"),
            total_paid_gbp=d.get("total_paid_gbp", 0.0),
            special_requests=d.get("special_requests", ""),
            passengers=[Passenger(**p) for p in d.get("passengers", [])],
            segments=[Segment(**s) for s in d.get("segments", [])],
            final_destination=d.get("final_destination", ""),
            disruption=d.get("disruption"),
        )


class CaseRecord(BaseModel):
    case_id: str
    booking_ref: str | None = None
    passenger_identity_confirmed: bool = False
    identity_method: str = ""
    safety_issues: list[str] = Field(default_factory=list)
    flight_record: dict[str, Any] | None = None
    entitlement: dict[str, Any] | None = None
    policy_sections_consulted: list[str] = Field(default_factory=list)
    decision: str = ""
    reasoning: str = ""
    actions_taken: list[dict[str, Any]] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    human_followup: str = ""
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
