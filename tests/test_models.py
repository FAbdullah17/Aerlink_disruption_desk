from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import Booking, CaseRecord, Passenger, Segment


def test_passenger_valid():
    p = Passenger(
        passenger_id="P1",
        given_name="John",
        surname="Smith",
        passenger_type="ADULT",
        age=42,
    )
    assert p.passenger_id == "P1"
    assert p.cabin_booked == "ECONOMY"
    assert p.assistance is None


def test_passenger_defaults():
    p = Passenger(
        passenger_id="P1",
        given_name="A",
        surname="B",
        passenger_type="ADULT",
        age=30,
    )
    assert p.cabin_booked == "ECONOMY"
    assert p.cabin_flown is None
    assert p.assistance is None


def test_passenger_rejects_missing_fields():
    with pytest.raises(ValidationError):
        Passenger(passenger_id="P1")  # type: ignore[call-arg]


def test_segment_valid():
    s = Segment(
        segment_id="S1",
        flight_no="AK123",
        date="2026-08-04",
        origin="LHR",
        destination="BCN",
    )
    assert s.segment_fare_gbp == 0.0
    assert s.is_affected is False


def test_booking_from_dict():
    d = {
        "booking_ref": "AER-123456",
        "customer_id": "CUS-1",
        "contact_email": "test@example.com",
        "passengers": [
            {"passenger_id": "P1", "given_name": "A", "surname": "B", "passenger_type": "ADULT", "age": 30}
        ],
        "segments": [
            {"segment_id": "S1", "flight_no": "AK1", "date": "2026-08-04", "origin": "LHR", "destination": "BCN"}
        ],
    }
    b = Booking.from_dict(d)
    assert b.booking_ref == "AER-123456"
    assert len(b.passengers) == 1
    assert b.passengers[0].given_name == "A"
    assert b.total_paid_gbp == 0.0


def test_booking_from_dict_minimal():
    d = {
        "booking_ref": "AER-000000",
        "customer_id": "CUS-1",
        "contact_email": "a@b.com",
    }
    b = Booking.from_dict(d)
    assert b.passengers == []
    assert b.segments == []
    assert b.disruption is None


def test_case_record_defaults():
    r = CaseRecord(case_id="test-01")
    assert r.booking_ref is None
    assert r.decision == ""
    assert r.actions_taken == []
    assert r.tokens_in == 0
