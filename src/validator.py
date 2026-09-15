from __future__ import annotations

import re
from typing import Any

from .models import Booking


EXTRAORDINARY_CAUSES = {
    "WEATHER", "ATC_RESTRICTION", "ATC_STRIKE", "SECURITY",
    "POLITICAL", "BIRDSTRIKE", "MEDICAL_DIVERSION",
}

BAND_COMPENSATION_GBP = {"A": 220.0, "B": 350.0, "C": 520.0}
BAND_REDUCTION_THRESHOLD_MIN = {"A": 240, "B": 300, "C": 360}


def validate_actions(
    actions: list[dict[str, Any]],
    booking: Booking,
    entitlement: dict[str, Any] | None,
    flight: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Cross-check every proposed action against API data and policy limits.
    Returns (approved_actions, issues_found).
    Actions that fail validation are replaced with escalations.
    """
    approved = []
    issues = []

    for action in actions:
        action_type = action.get("type", "")

        # Skip zero-amount financial actions — no point executing them
        if action_type in ("compensation", "refund", "goodwill"):
            amount = action.get("amount_gbp", 0)
            if amount <= 0:
                continue

        if action_type == "compensation":
            ok, issue = _validate_compensation(action, booking, entitlement, flight)
            if ok:
                approved.append(action)
            else:
                issues.append(issue)
                approved.append(_escalate_instead(action, issue))

        elif action_type == "refund":
            ok, issue = _validate_refund(action, booking)
            if ok:
                approved.append(action)
            else:
                issues.append(issue)
                approved.append(_escalate_instead(action, issue))

        elif action_type == "goodwill":
            ok, issue = _validate_goodwill(action, booking)
            if ok:
                approved.append(action)
            else:
                issues.append(issue)
                approved.append(_escalate_instead(action, issue))

        elif action_type == "rebook":
            ok, issue = _validate_rebooking(action, booking)
            if ok:
                approved.append(action)
            else:
                issues.append(issue)
                approved.append(_escalate_instead(action, issue))

        elif action_type == "hotel_voucher":
            ok, issue = _validate_hotel_voucher(action, booking)
            if ok:
                approved.append(action)
            else:
                issues.append(issue)
                approved.append(_escalate_instead(action, issue))

        else:
            approved.append(action)

    return approved, issues


def _validate_compensation(action: dict, booking: Booking, entitlement: dict | None, flight: dict | None) -> tuple[bool, str]:
    amount = action.get("amount_gbp", 0)
    passenger_ids = action.get("passenger_ids", [])
    reason = action.get("reason", "").lower()

    if not entitlement:
        return False, "NO_ENTITLEMENT_DATA: Cannot validate compensation without entitlement calculation"

    if entitlement.get("status") == "NO_DISRUPTION_RECORDED":
        return False, "NO_DISRUPTION: Entitlement calculator shows no disruption recorded"

    # Check if this is a downgrade reimbursement (reason mentions "downgrade")
    is_downgrade = "downgrade" in reason

    if is_downgrade:
        # Validate against downgrade_reimbursement_gbp per passenger
        for pid in passenger_ids:
            pax = next((p for p in entitlement.get("passengers", []) if p["passenger_id"] == pid), None)
            if pax:
                expected = pax.get("downgrade_reimbursement_gbp", 0)
                if amount != expected:
                    return False, f"DOWNGRADE_MISMATCH: LLM says £{amount:.2f} for {pid}, entitlement calculator says £{expected:.2f}"
            # If passenger not found in entitlement, the amount might still be valid if entitlement is incomplete
    else:
        # Validate against compensation_gbp per passenger
        for pid in passenger_ids:
            pax = next((p for p in entitlement.get("passengers", []) if p["passenger_id"] == pid), None)
            if pax:
                expected = pax.get("compensation_gbp", 0)
                if amount != expected:
                    return False, f"COMP_MISMATCH: LLM says £{amount:.2f} for {pid}, entitlement calculator says £{expected:.2f}"

    # Verify cause is within control for compensation to be payable
    comp = entitlement.get("compensation", {})
    if comp.get("status") == "NOT_PAYABLE" and not is_downgrade:
        cause = flight.get("cause_code") if flight else None
        if cause in EXTRAORDINARY_CAUSES:
            return False, f"NOT_PAYABLE_EXTRAORDINARY: Cause is {cause} which is extraordinary - no compensation under S5"

    return True, ""


def _validate_refund(action: dict, booking: Booking) -> tuple[bool, str]:
    amount = action.get("amount_gbp", 0)
    passenger_ids = action.get("passenger_ids", [])

    if not passenger_ids:
        return False, "NO_PASSENGER_IDS: Refund must specify which passengers"

    if amount > booking.total_paid_gbp:
        return False, f"AMOUNT_EXCEEDS_TOTAL: £{amount:.2f} exceeds booking total of £{booking.total_paid_gbp:.2f}"

    return True, ""


def _validate_goodwill(action: dict, booking: Booking) -> tuple[bool, str]:
    amount = action.get("amount_gbp", 0)

    if amount > 150:
        return False, f"GOODWILL_EXCEEDS_REP_AUTHORITY: £{amount:.2f} exceeds £150 representative limit - needs supervisor (S12.1)"

    if amount < 0:
        return False, "NEGATIVE_AMOUNT: Goodwill amount cannot be negative"

    return True, ""


def _validate_rebooking(action: dict, booking: Booking) -> tuple[bool, str]:
    cabin = action.get("cabin", "")
    fare = action.get("fare_gbp", 0)
    passenger_ids = action.get("passenger_ids", [])
    option_id = action.get("option_id", "")
    flight_no = action.get("flight_no", "")
    date = action.get("date", "")

    if not passenger_ids:
        return False, "NO_PASSENGER_IDS: Rebooking must specify which passengers"

    if not option_id:
        return False, "NO_OPTION_ID: Rebooking requires a specific availability option_id from the API"

    if not flight_no or not date:
        return False, "MISSING_FLIGHT_OR_DATE: Rebooking requires flight_no and date"

    # Check if partner rebooking
    is_partner = not flight_no.startswith("AK")

    if is_partner:
        per_pax = fare / len(passenger_ids) if passenger_ids else fare
        if per_pax > 600:
            return False, f"PARTNER_REBOOKING_OVER_600: £{per_pax:.2f}/passenger exceeds £600 limit - needs manager (S8.2)"
        if per_pax > 0:
            return False, f"PARTNER_REBOOKING: £{per_pax:.2f}/passenger needs supervisor authorisation (S8.2)"

    # Check cabin match
    original_cabin = booking.passengers[0].cabin_booked if booking.passengers else "ECONOMY"
    if cabin and cabin != original_cabin:
        return False, f"CABIN_CHANGE: Rebooking from {original_cabin} to {cabin} needs supervisor authorisation (S12.1)"

    return True, ""


def _validate_hotel_voucher(action: dict, booking: Booking) -> tuple[bool, str]:
    station = action.get("station", "")
    night = action.get("night", "")
    passenger_ids = action.get("passenger_ids", [])

    if not station or not night:
        return False, "MISSING_STATION_OR_NIGHT: Hotel voucher requires station and night"

    if not passenger_ids:
        return False, "NO_PASSENGER_IDS: Hotel voucher must specify passengers"

    # Night must be in YYYY-MM-DD format
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", night):
        return False, f"INVALID_NIGHT_FORMAT: {night} - must be YYYY-MM-DD"

    return True, ""


def _escalate_instead(action: dict, reason: str) -> dict:
    """Replace a failed action with an escalation."""
    return {
        "type": "escalate",
        "summary": f"Action {action.get('type', 'unknown')} failed validation: {reason}",
        "requested_decision": f"Review the proposed {action.get('type', 'unknown')} action",
        "queue": "SUPERVISOR",
        "recommendation": reason,
        "original_action": action,
    }
