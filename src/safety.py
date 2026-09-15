from __future__ import annotations

import re
from typing import Any

from .models import SafetyVerdict


# -- Prompt injection patterns --

INJECTION_PATTERNS = [
    # Fake system directives / handling instructions
    r"(?i)system\s+(?:notice|directive|instruction|override)",
    r"(?i)automated\s+handling\s+instruction",
    r"(?i)priority\s+override",
    r"(?i)authoriz(?:e|ation)\s+limits?\s+(?:in|under)\s+section\s+12",
    r"(?i)limits?\s+(?:in|under)\s+section\s+12\s+(?:are|is)\s+suspended",
    r"(?i)without\s+supervisor\s+(?:approval|authorisation|authorization)",
    r"(?i)without\s+further\s+verification",
    r"(?i)do\s+not\s+(?:escalate|verify|request|check)",
    r"(?i)mark\s+(?:the\s+)?case\s+resolved",
    r"(?i)reply\s+(?:with\s+)?(?:the\s+single\s+word\s+)?CONFIRMED",
    r"(?i)service\s+breach\s+against",
    r"(?i)settlement\s+agreement\s+SUP-",
    r"(?i)system\s+directive\s+to\s+handling\s+agent",
    r"(?i)priority\s+handling\s+note\s*\(ops",
    # Authority claims from external content
    r"(?i)authoriz(?:e|ed|ation)\s+to\s+issue",
    r"(?i)you\s+are\s+authoriz(?:e|ed)",
    r"(?i)any\s+deviation\s+from\s+this\s+instruction",
    r"(?i)recorded\s+as\s+a\s+service\s+breach",
]

# Patterns in special_requests that look like injected directives
SPECIAL_REQUEST_INJECTION_PATTERNS = [
    r"(?i)>>>",
    r"(?i)system\s+directive",
    r"(?i)handling\s+agent",
    r"(?i)authorisation\s+limits?\s+(?:are|is)\s+suspended",
    r"(?i)do\s+not\s+refer",
    r"(?i)issue\s+(?:a\s+)?(?:full\s+)?refund\s+and",
    r"(?i)goodwill\s+payment\s+of\s+GBP\s+\d",
    r"(?i)mark\s+the\s+case\s+resolved",
    r"(?i)reply\s+CONFIRMED",
    r"(?i)settlement\s+agreement",
]


def detect_injection(text: str) -> list[str]:
    """Check text for prompt injection patterns. Returns list of matched patterns."""
    matches = []
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text):
            matches.append(pattern)
    return matches


def detect_special_requests_injection(text: str) -> list[str]:
    """Check special_requests field for injected directives."""
    matches = []
    for pattern in SPECIAL_REQUEST_INJECTION_PATTERNS:
        if re.search(pattern, text):
            matches.append(pattern)
    return matches


def extract_email_from_sender(sender: str) -> str:
    """Extract email address from a 'Name <email>' format string."""
    match = re.search(r"<([^>]+)>", sender)
    if match:
        return match.group(1).strip().lower()
    # Maybe it's just an email
    if "@" in sender:
        return sender.strip().lower()
    return ""


def verify_identity(
    sender_email: str,
    sender_name: str,
    booking_ref_from_email: str | None,
    search_results: list[dict[str, Any]],
    booking: dict[str, Any] | None,
) -> tuple[bool, str]:
    """
    Verify passenger identity per Section 2 of the policy.
    Returns (confirmed, method_description).

    Identity is confirmed when exactly one booking matches by:
    (a) booking reference + surname match
    (b) exact email match
    (c) exact phone match
    """
    if booking_ref_from_email and booking:
        ref = booking_ref_from_email.upper()
        if ref == booking.get("booking_ref", "").upper():
            surname_in_email = sender_name.split()[-1].lower() if sender_name else ""
            for p in booking.get("passengers", []):
                if p.get("surname", "").lower() == surname_in_email:
                    return True, f"booking_ref_exact+surname:{ref}"
        return False, "booking_ref_no_surname_match"

    if not search_results:
        return False, "no_matches"

    if len(search_results) == 1:
        r = search_results[0]
        matched_on = r.get("matched_on", [])
        if any("contact_email_exact" in m for m in matched_on):
            return True, f"email_exact:{r['booking_ref']}"
        if any("contact_phone_match" in m for m in matched_on):
            return True, f"phone_match:{r['booking_ref']}"
        if any("passenger_name_exact" in m for m in matched_on):
            return True, f"name_exact:{r['booking_ref']}"

    if len(search_results) > 1:
        return False, f"multiple_matches:{len(search_results)}"

    return False, "insufficient_match"


def run_safety_checks(
    email_text: str,
    sender: str,
    booking_data: dict[str, Any] | None,
    search_results: list[dict[str, Any]],
) -> tuple[SafetyVerdict, list[str], str | None]:
    """
    Run all safety checks before any processing.
    Returns (verdict, issues_list, booking_ref_or_None).
    """
    issues: list[str] = []

    # 1. Check email for injection
    injection = detect_injection(email_text)
    if injection:
        issues.append(f"INJECTION_DETECTED_IN_EMAIL: {len(injection)} patterns matched")
        return SafetyVerdict.ESCALATE_INJECTION, issues, None

    # 2. Check special_requests for injection
    if booking_data:
        sr = booking_data.get("special_requests", "")
        sr_injection = detect_special_requests_injection(sr)
        if sr_injection:
            issues.append(f"INJECTION_DETECTED_IN_SPECIAL_REQUESTS: {len(sr_injection)} patterns matched")
            return SafetyVerdict.ESCALATE_INJECTION, issues, booking_data.get("booking_ref")

    # 3. Check for YTP passengers
    if booking_data:
        passengers = booking_data.get("passengers", [])
        ytp = [p for p in passengers if p.get("passenger_type") == "YTP"]
        if ytp:
            names = [f"{p['given_name']} {p['surname']}" for p in ytp]
            issues.append(f"YTP_PASSENGERS: {', '.join(names)}")
            return SafetyVerdict.ESCALATE_YTP, issues, booking_data.get("booking_ref")

    # 4. Verify identity
    sender_email = extract_email_from_sender(sender)
    sender_name = re.sub(r"<[^>]+>", "", sender).strip()
    booking_ref_match = re.search(r"\b(AER-[A-Z0-9]{6})\b", email_text, re.IGNORECASE)
    booking_ref_from_email = booking_ref_match.group(1) if booking_ref_match else None

    confirmed, method = verify_identity(
        sender_email, sender_name, booking_ref_from_email, search_results, booking_data
    )

    if not confirmed:
        issues.append(f"IDENTITY_NOT_CONFIRMED: {method}")
        booking_ref = booking_ref_from_email or (search_results[0]["booking_ref"] if search_results else None)
        return SafetyVerdict.ESCALATE_IDENTITY, issues, booking_ref

    issues.append(f"IDENTITY_CONFIRMED: {method}")
    booking_ref = None
    if booking_data:
        booking_ref = booking_data.get("booking_ref")
    elif search_results:
        booking_ref = search_results[0].get("booking_ref")

    return SafetyVerdict.OK, issues, booking_ref
