from __future__ import annotations

from src.policy import format_policy_sections, get_relevant_policy_sections


SAMPLE_POLICY = """# Aerlink Passenger Care Policy

## 1.1 Disruption events

A disruption event is any of the following.

## 2.1 Confirmation standards

Identity is confirmed when exactly one booking matches.

## 3.2 Extraordinary circumstances

The following cause codes are extraordinary.

## 4.2 Entitlements

Once triggered, the passenger is entitled to meals.

## 5.3 Standard amounts

Band A: 220 pounds. Band B: 350 pounds.

## 7.1 Entitlement

A passenger whose flight is cancelled is entitled to a refund.
"""


class TestGetRelevantSections:
    def test_cancellation_fetches_basic_sections(self):
        booking = {"disruption": {"affected_segment": "S1"}}
        flight = {"status": "CANCELLED"}
        sections = get_relevant_policy_sections("My flight was cancelled", booking, flight, None)
        assert "3" in sections
        assert "4" in sections
        assert "5" in sections
        assert "6" in sections
        assert "7" in sections

    def test_delay_fetches_sections(self):
        sections = get_relevant_policy_sections("My flight was late", {}, {"status": "DELAYED"}, None)
        assert "3" in sections
        assert "4" in sections
        assert "5" in sections

    def test_compensation_request(self):
        sections = get_relevant_policy_sections("I want compensation", {}, None, None)
        assert "5" in sections

    def test_hotel_request(self):
        sections = get_relevant_policy_sections("I need somewhere to sleep", {}, None, None)
        assert "4" in sections

    def test_multi_passenger_sections(self):
        booking = {
            "passengers": [
                {"given_name": "A", "surname": "B"},
                {"given_name": "C", "surname": "D"},
            ]
        }
        sections = get_relevant_policy_sections("My flight was cancelled", booking, {"status": "CANCELLED"}, None)
        assert "6.1" in sections
        assert "7.3" in sections
        assert "15.1" in sections

    def test_special_assistance_from_booking(self):
        booking = {
            "passengers": [
                {"given_name": "A", "surname": "B", "assistance": "WCHR"},
            ]
        }
        sections = get_relevant_policy_sections("Help me", booking, None, None)
        assert "14" in sections
        assert "14.4" in sections

    def test_wheelchair_in_email(self):
        sections = get_relevant_policy_sections("I need wheelchair assistance", {}, None, None)
        assert "14" in sections
        assert "14.4" in sections

    def test_extraordinary_cause(self):
        flight = {"cause_code": "WEATHER", "status": "CANCELLED"}
        sections = get_relevant_policy_sections("My flight was cancelled", {"passengers": []}, flight, None)
        assert "3.2" in sections

    def test_partner_rebooking(self):
        sections = get_relevant_policy_sections("Can you put me on another airline?", {}, None, None)
        assert "8" in sections
        assert "8.1" in sections
        assert "8.2" in sections

    def test_goodwill_request(self):
        sections = get_relevant_policy_sections("I want goodwill for the hassle", {}, None, None)
        assert "11" in sections
        assert "11.1" in sections


class TestFormatPolicySections:
    def test_extracts_matching_sections(self):
        result = format_policy_sections(SAMPLE_POLICY, ["3.2", "5.3"])
        assert "Extraordinary circumstances" in result
        assert "Standard amounts" in result

    def test_empty_for_no_match(self):
        result = format_policy_sections(SAMPLE_POLICY, ["99.99"])
        assert result == ""

    def test_preserves_content(self):
        result = format_policy_sections(SAMPLE_POLICY, ["4.2"])
        assert "meals" in result.lower()
