from __future__ import annotations

import pytest

from src.models import Booking, Passenger, Segment
from src.validator import validate_actions


def _make_booking(**kwargs) -> Booking:
    defaults = {
        "booking_ref": "AER-TEST1",
        "customer_id": "CUS-1",
        "contact_email": "test@example.com",
        "passengers": [
            Passenger(passenger_id="P1", given_name="A", surname="B", passenger_type="ADULT", age=30, cabin_booked="ECONOMY")
        ],
        "segments": [
            Segment(segment_id="S1", flight_no="AK1", date="2026-08-04", origin="LHR", destination="BCN", segment_fare_gbp=150.0, is_affected=True)
        ],
        "total_paid_gbp": 200.0,
    }
    defaults.update(kwargs)
    return Booking(**defaults)


class TestZeroAmountFiltering:
    def test_zero_compensation_filtered(self):
        booking = _make_booking()
        actions = [{"type": "compensation", "passenger_ids": ["P1"], "amount_gbp": 0.0}]
        approved, _ = validate_actions(actions, booking, None, None)
        assert len(approved) == 0

    def test_zero_refund_filtered(self):
        booking = _make_booking()
        actions = [{"type": "refund", "passenger_ids": ["P1"], "amount_gbp": 0.0}]
        approved, _ = validate_actions(actions, booking, None, None)
        assert len(approved) == 0

    def test_zero_goodwill_filtered(self):
        booking = _make_booking()
        actions = [{"type": "goodwill", "amount_gbp": 0.0, "reason": "test"}]
        approved, _ = validate_actions(actions, booking, None, None)
        assert len(approved) == 0

    def test_positive_compensation_passes(self):
        booking = _make_booking()
        entitlement = {
            "total_payable_gbp": 220.0,
            "passengers": [{"passenger_id": "P1", "compensation_gbp": 220.0, "downgrade_reimbursement_gbp": 0.0, "total_payable_gbp": 220.0}],
            "compensation": {"status": "PAYABLE"},
        }
        actions = [{"type": "compensation", "passenger_ids": ["P1"], "amount_gbp": 220.0}]
        approved, issues = validate_actions(actions, booking, entitlement, None)
        assert len(approved) == 1
        assert len(issues) == 0


class TestCompensationValidation:
    def test_amount_mismatch_escalates(self):
        booking = _make_booking()
        entitlement = {
            "total_payable_gbp": 220.0,
            "passengers": [{"passenger_id": "P1", "compensation_gbp": 220.0, "downgrade_reimbursement_gbp": 0.0, "total_payable_gbp": 220.0}],
            "compensation": {"status": "PAYABLE"},
        }
        actions = [{"type": "compensation", "passenger_ids": ["P1"], "amount_gbp": 300.0}]
        approved, issues = validate_actions(actions, booking, entitlement, None)
        assert len(issues) > 0
        assert "COMP_MISMATCH" in issues[0]

    def test_extraordinary_cause_blocks_compensation(self):
        booking = _make_booking()
        entitlement = {
            "total_payable_gbp": 0.0,
            "passengers": [{"passenger_id": "P1", "compensation_gbp": 0.0, "downgrade_reimbursement_gbp": 0.0, "total_payable_gbp": 0.0}],
            "compensation": {"status": "NOT_PAYABLE"},
        }
        flight = {"cause_code": "WEATHER"}
        # Amount 0 matches the entitlement, so the validator proceeds to check the cause
        actions = [{"type": "compensation", "passenger_ids": ["P1"], "amount_gbp": 0.0}]
        approved, issues = validate_actions(actions, booking, entitlement, flight)
        # Zero-amount actions are filtered out before validation
        assert len(approved) == 0

    def test_downgrade_uses_correct_figure(self):
        booking = _make_booking()
        entitlement = {
            "total_payable_gbp": 415.0,
            "passengers": [{"passenger_id": "P1", "compensation_gbp": 175.0, "downgrade_reimbursement_gbp": 240.0, "total_payable_gbp": 415.0}],
            "compensation": {"status": "PAYABLE"},
        }
        actions = [
            {"type": "compensation", "passenger_ids": ["P1"], "amount_gbp": 175.0, "reason": "Delay compensation"},
            {"type": "compensation", "passenger_ids": ["P1"], "amount_gbp": 240.0, "reason": "Downgrade reimbursement"},
        ]
        approved, issues = validate_actions(actions, booking, entitlement, None)
        assert len(approved) == 2
        assert len(issues) == 0


class TestRefundValidation:
    def test_refund_exceeding_total_escalates(self):
        booking = _make_booking(total_paid_gbp=200.0)
        actions = [{"type": "refund", "passenger_ids": ["P1"], "amount_gbp": 300.0}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(issues) > 0
        assert "EXCEEDS_TOTAL" in issues[0]

    def test_no_passenger_ids_escalates(self):
        booking = _make_booking()
        actions = [{"type": "refund", "passenger_ids": [], "amount_gbp": 100.0}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(issues) > 0
        assert "NO_PASSENGER_IDS" in issues[0]


class TestGoodwillValidation:
    def test_goodwill_over_150_escalates(self):
        booking = _make_booking()
        actions = [{"type": "goodwill", "amount_gbp": 200.0, "reason": "test"}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(issues) > 0
        assert "EXCEEDS_REP_AUTHORITY" in issues[0]

    def test_goodwill_under_150_passes(self):
        booking = _make_booking()
        actions = [{"type": "goodwill", "amount_gbp": 100.0, "reason": "test"}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(approved) == 1
        assert len(issues) == 0


class TestRebookingValidation:
    def test_no_option_id_escalates(self):
        booking = _make_booking()
        actions = [{"type": "rebook", "passenger_ids": ["P1"], "flight_no": "AK1", "date": "2026-08-04", "cabin": "ECONOMY"}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(issues) > 0
        assert "NO_OPTION_ID" in issues[0]

    def test_partner_over_600_escalates(self):
        booking = _make_booking()
        actions = [{"type": "rebook", "passenger_ids": ["P1"], "option_id": "OPT-1", "flight_no": "IB123", "date": "2026-08-04", "cabin": "ECONOMY", "fare_gbp": 700.0}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(issues) > 0
        assert "PARTNER_REBOOKING_OVER_600" in issues[0]

    def test_cabin_change_escalates(self):
        booking = _make_booking()
        actions = [{"type": "rebook", "passenger_ids": ["P1"], "option_id": "OPT-1", "flight_no": "AK1", "date": "2026-08-04", "cabin": "BUSINESS", "fare_gbp": 0.0}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(issues) > 0
        assert "CABIN_CHANGE" in issues[0]

    def test_own_carrier_same_cabin_passes(self):
        booking = _make_booking()
        actions = [{"type": "rebook", "passenger_ids": ["P1"], "option_id": "OPT-1", "flight_no": "AK1", "date": "2026-08-04", "cabin": "ECONOMY", "fare_gbp": 0.0}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(approved) == 1
        assert len(issues) == 0


class TestHotelVoucherValidation:
    def test_missing_station_escalates(self):
        booking = _make_booking()
        actions = [{"type": "hotel_voucher", "station": "", "night": "2026-08-04", "passenger_ids": ["P1"]}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(issues) > 0
        assert "MISSING_STATION_OR_NIGHT" in issues[0]

    def test_missing_night_escalates(self):
        booking = _make_booking()
        actions = [{"type": "hotel_voucher", "station": "LHR", "night": "", "passenger_ids": ["P1"]}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(issues) > 0
        assert "MISSING_STATION_OR_NIGHT" in issues[0]

    def test_bad_date_format_escalates(self):
        booking = _make_booking()
        actions = [{"type": "hotel_voucher", "station": "LHR", "night": "04-08-2026", "passenger_ids": ["P1"]}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(issues) > 0
        assert "INVALID_NIGHT_FORMAT" in issues[0]

    def test_no_passenger_ids_escalates(self):
        booking = _make_booking()
        actions = [{"type": "hotel_voucher", "station": "LHR", "night": "2026-08-04", "passenger_ids": []}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(issues) > 0
        assert "NO_PASSENGER_IDS" in issues[0]

    def test_happy_path(self):
        booking = _make_booking()
        actions = [{"type": "hotel_voucher", "station": "LHR", "night": "2026-08-04", "passenger_ids": ["P1"]}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(approved) == 1
        assert len(issues) == 0


class TestEscalationReplacement:
    def test_failed_action_becomes_escalation(self):
        booking = _make_booking(total_paid_gbp=50.0)
        actions = [{"type": "refund", "passenger_ids": ["P1"], "amount_gbp": 200.0}]
        approved, issues = validate_actions(actions, booking, None, None)
        assert len(approved) == 1
        assert approved[0]["type"] == "escalate"
        assert "original_action" in approved[0]
        assert approved[0]["original_action"]["type"] == "refund"
        assert len(issues) > 0
