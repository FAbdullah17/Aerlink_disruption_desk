from __future__ import annotations

from src.prompts import (
    SYSTEM_PROMPT,
    _format_booking,
    _json,
    build_case_prompt,
)


class TestJson:
    def test_serializes_dict(self):
        assert _json({"a": 1}) == '{\n  "a": 1\n}'

    def test_handles_non_stringable(self):
        class Fake:
            def __str__(self):
                return "fake_val"
        result = _json({"x": Fake()})
        assert "fake_val" in result


class TestFormatBooking:
    def test_includes_key_fields(self):
        booking = {
            "booking_ref": "AER-TEST1",
            "customer_id": "CUS-1",
            "contact_email": "test@example.com",
            "tier": "SILVER",
            "total_paid_gbp": 200.0,
            "passengers": [{"passenger_id": "P1", "given_name": "A", "surname": "B", "passenger_type": "ADULT", "age": 30}],
            "segments": [{"segment_id": "S1", "flight_no": "AK1", "date": "2026-08-04", "origin": "LHR", "destination": "BCN", "segment_fare_gbp": 150.0}],
            "final_destination": "BCN",
            "disruption": None,
        }
        result = _format_booking(booking)
        assert "AER-TEST1" in result
        assert "CUS-1" in result
        assert "test@example.com" in result
        assert "SILVER" in result
        assert "200.00" in result
        assert "ADULT" in result
        assert "AK1" in result
        assert "Disruption: none recorded" in result

    def test_includes_special_requests(self):
        booking = {
            "booking_ref": "AER-TEST1",
            "customer_id": "CUS-1",
            "contact_email": "test@example.com",
            "tier": "BRONZE",
            "total_paid_gbp": 100.0,
            "passengers": [],
            "segments": [],
            "special_requests": "WCHR",
        }
        result = _format_booking(booking)
        assert "WCHR" in result

    def test_includes_disruption(self):
        booking = {
            "booking_ref": "AER-TEST1",
            "customer_id": "CUS-1",
            "contact_email": "test@example.com",
            "tier": "GOLD",
            "total_paid_gbp": 100.0,
            "passengers": [],
            "segments": [],
            "disruption": {
                "affected_segment": "S1",
                "rerouted_onto": "AK529",
                "arrival_delay_minutes": 180,
                "informed_days_before": 0,
            },
        }
        result = _format_booking(booking)
        assert "rerouted_onto=AK529" in result
        assert "180" in result


class TestBuildCasePrompt:
    def test_includes_email_and_booking(self):
        booking = {
            "booking_ref": "AER-TEST1",
            "customer_id": "CUS-1",
            "contact_email": "test@example.com",
            "tier": "SILVER",
            "total_paid_gbp": 200.0,
            "passengers": [],
            "segments": [],
        }
        result = build_case_prompt(
            "My flight was cancelled", booking, None, None, None, "Policy text here"
        )
        assert "INBOUND PASSENGER MESSAGE" in result
        assert "My flight was cancelled" in result
        assert "BOOKING RECORD" in result
        assert "AER-TEST1" in result
        assert "RELEVANT POLICY SECTIONS" in result
        assert "Policy text here" in result

    def test_includes_flight_when_present(self):
        booking = {"booking_ref": "AER-TEST1", "customer_id": "CUS-1", "contact_email": "t@t.com", "tier": "SILVER", "total_paid_gbp": 100.0, "passengers": [], "segments": []}
        flight = {"flight_no": "AK1", "status": "CANCELLED"}
        result = build_case_prompt("Help", booking, flight, None, None, "")
        assert "FLIGHT RECORD" in result
        assert "CANCELLED" in result

    def test_includes_entitlement_when_present(self):
        booking = {"booking_ref": "AER-TEST1", "customer_id": "CUS-1", "contact_email": "t@t.com", "tier": "SILVER", "total_paid_gbp": 100.0, "passengers": [], "segments": []}
        entitlement = {"total_payable_gbp": 220.0, "compensation": {"status": "PAYABLE"}}
        result = build_case_prompt("Help", booking, None, entitlement, None, "")
        assert "ENTITLEMENT CALCULATION" in result
        assert "220.0" in result

    def test_includes_availability_when_present(self):
        booking = {"booking_ref": "AER-TEST1", "customer_id": "CUS-1", "contact_email": "t@t.com", "tier": "SILVER", "total_paid_gbp": 100.0, "passengers": [], "segments": []}
        availability = {"total_results": 2, "results": [{"option_id": "OPT-1", "flight_no": "AK2", "departure_local": "10:00", "arrival_local": "13:00", "cabin": "ECONOMY", "seats_available": 5, "fare_gbp": 0.0}]}
        result = build_case_prompt("Help", booking, None, None, None, "", availability=availability)
        assert "AVAILABLE FLIGHTS" in result
        assert "OPT-1" in result

    def test_includes_hotel_allocation_when_present(self):
        booking = {"booking_ref": "AER-TEST1", "customer_id": "CUS-1", "contact_email": "t@t.com", "tier": "SILVER", "total_paid_gbp": 100.0, "passengers": [], "segments": []}
        hotel = {"station": "LHR", "rooms_available": 3}
        result = build_case_prompt("Help", booking, None, None, None, "", hotel_allocation=hotel)
        assert "HOTEL ALLOCATION" in result

    def test_includes_disruption_feed_when_present(self):
        booking = {"booking_ref": "AER-TEST1", "customer_id": "CUS-1", "contact_email": "t@t.com", "tier": "SILVER", "total_paid_gbp": 100.0, "passengers": [], "segments": []}
        feed = {"events": [{"flight_no": "AK1", "status": "CANCELLED"}]}
        result = build_case_prompt("Help", booking, None, None, None, "", disruption_feed=feed)
        assert "NETWORK DISRUPTION FEED" in result


class TestSystemPrompt:
    def test_contains_critical_rules(self):
        assert "operational record is authoritative" in SYSTEM_PROMPT
        assert "ARRIVAL DELAY" in SYSTEM_PROMPT
        assert "YTP" in SYSTEM_PROMPT
        assert "ACTION RULES" in SYSTEM_PROMPT
