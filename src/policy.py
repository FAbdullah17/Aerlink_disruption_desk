from __future__ import annotations

from typing import Any


def get_relevant_policy_sections(email_text: str, booking: dict[str, Any], flight: dict[str, Any] | None, entitlement: dict[str, Any] | None) -> list[str]:
    """
    Determine which policy sections are relevant based on the case situation.
    Returns section numbers to fetch.
    """
    sections = ["1.1", "1.2", "1.3", "2.1"]  # Always include scope, definitions, bands, identity

    email_lower = email_text.lower()
    cause = flight.get("cause_code") if flight else None
    status = flight.get("status") if flight else None
    disruption = booking.get("disruption")

    # Cancellation
    if status == "CANCELLED" or "cancelled" in email_lower:
        sections.extend(["3", "4", "5", "6", "7"])
        if disruption and disruption.get("rerouted_onto"):
            sections.append("5.4")

    # Delay
    if status == "DELAYED" or "delay" in email_lower or "late" in email_lower:
        sections.extend(["3", "4", "5"])
        dep_delay = flight.get("departure_delay_minutes") if flight else None
        if dep_delay and dep_delay >= 300:
            sections.append("6")

    # Compensation
    if any(w in email_lower for w in ["compensation", "owed", "entitled", "pay me", "claim"]):
        sections.extend(["5", "5.1", "5.2", "5.3", "5.4"])

    # Downgrade
    if any(w in email_lower for w in ["downgrade", "economy", "business"]) or \
       (entitlement and any(p.get("downgrade_reimbursement_gbp", 0) > 0 for p in entitlement.get("passengers", []))):
        sections.extend(["9", "9.1", "9.3", "9.4"])

    # Refund
    if any(w in email_lower for w in ["refund", "money back", "return my"]):
        sections.extend(["7", "7.1", "7.2", "7.3"])

    # Goodwill
    if any(w in email_lower for w in ["goodwill", "gesture", "in addition", "beyond"]):
        sections.extend(["11", "11.1", "11.2", "11.3", "11.4", "11.5"])

    # Hotel / care
    if any(w in email_lower for w in ["hotel", "sleep", "accommodation", "somewhere to stay"]):
        sections.extend(["4", "4.2", "4.5"])

    # Partner re-routing
    if any(w in email_lower for w in ["partner", "other airline", "not aerlink"]):
        sections.extend(["8", "8.1", "8.2", "8.3"])

    # Extraordinary cause
    extraordinary = {"WEATHER", "ATC_RESTRICTION", "ATC_STRIKE", "SECURITY", "POLITICAL", "BIRDSTRIKE", "MEDICAL_DIVERSION"}
    if cause and cause in extraordinary:
        sections.extend(["3.2"])

    # Authority limits (for anything involving payments)
    if any(w in email_lower for w in ["pay", "refund", "rebook", "voucher"]):
        sections.append("12")

    # Multi-passenger
    passengers = booking.get("passengers", [])
    if len(passengers) > 1:
        sections.extend(["6.1", "7.3", "15.1"])

    # Special assistance
    has_assistance = any(p.get("assistance") for p in passengers)
    if has_assistance or "wheelchair" in email_lower or "assistance" in email_lower:
        sections.extend(["14", "14.4"])

    # Deduplicate while preserving order
    seen = set()
    result = []
    for s in sections:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def format_policy_sections(policy_doc: str, section_numbers: list[str]) -> str:
    """Extract relevant sections from the full policy document."""
    lines = policy_doc.split("\n")
    result_parts = []
    current_section = None
    current_section_num = None
    capturing = False

    for line in lines:
        if line.startswith("## "):
            if capturing and current_section_num in section_numbers:
                result_parts.append(current_section)
            current_section = line + "\n"
            capturing = False
            current_section_num = None
            for s in section_numbers:
                if line.startswith(f"## {s}.") or line.startswith(f"## {s} "):
                    capturing = True
                    current_section_num = s
                    break
        elif capturing:
            current_section += line + "\n"

    if capturing and current_section_num in section_numbers:
        result_parts.append(current_section)

    return "\n".join(result_parts)
