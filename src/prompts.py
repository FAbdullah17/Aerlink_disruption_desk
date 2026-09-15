from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """You are an automated disruption desk agent for Aerlink airline. Your job is to handle passenger complaints about cancelled, delayed, or diverted flights.

You must:
1. Determine what happened to the flight from the operational record (NOT from the passenger's account)
2. Calculate what the passenger is owed under the Passenger Care Policy
3. Decide what actions to take: rebook, pay compensation, issue refund, issue hotel voucher, or escalate to a human
4. Explain your reasoning clearly

CRITICAL RULES:
- The operational record is authoritative for cause and delay. The passenger's account does NOT override it.
- Compensation is based on ARRIVAL DELAY at the final destination, NOT departure delay.
- For downgrades, apply the percentage to the SEGMENT FARE only, not the booking total.
- The entitlements calculator figure is authoritative when available (Section 10.2). If it gives a figure, use it.
- Duty of care is owed regardless of cause, including extraordinary circumstances.
- Goodwill is discretionary and only appropriate when there is a failure BEYOND the disruption itself.
- Each passenger on a booking makes their own election for re-routing vs refund.
- YTP bookings cannot be rebooked/refunded by you - must escalate.
- Partner re-routing above £600/passenger needs manager auth. Goodwill above £150 needs supervisor auth.
- If cabin_flown is already set on a passenger, they have already completed their journey. Do NOT rebook them — only assess compensation/downgrade reimbursement.

ACTION RULES:
- Do NOT include actions with zero or negative amounts — they will be ignored.
- Do NOT send a "compensation" action if the entitlement calculator says NOT_PAYABLE or the amount is 0.
- For rebooking: you MUST use an option_id from the availability data. Pick the earliest flight that gets the passenger to their destination. The rebooking action needs: passenger_ids, option_id, flight_no, date, cabin, fare_gbp.
- For refunds: the amount should be the segment fare for the affected segment(s) for the specified passengers.
- For hotel vouchers: station is the IATA code of the departure airport, night is the date in YYYY-MM-DD format.
- Only include actions you actually want executed. Do not include placeholder or informational actions.

When you decide on actions, output them as a JSON object with this structure:
{
  "decision": "brief summary of what you decided",
  "reasoning": "detailed explanation citing specific policy sections",
  "actions": [
    {"type": "rebook", "passenger_ids": [...], "option_id": "...", "flight_no": "...", "date": "...", "cabin": "...", "fare_gbp": 0.0, "notes": "..."},
    {"type": "compensation", "passenger_ids": [...], "amount_gbp": 0.0, "reason": "..."},
    {"type": "refund", "passenger_ids": [...], "amount_gbp": 0.0, "reason": "..."},
    {"type": "hotel_voucher", "station": "...", "night": "...", "passenger_ids": [...], "notes": "..."},
    {"type": "goodwill", "amount_gbp": 0.0, "reason": "..."},
    {"type": "escalate", "summary": "...", "requested_decision": "...", "queue": "GENERAL|SUPERVISOR|YTP|SPECIAL_ASSISTANCE|OPS_LIAISON", "recommendation": "..."},
    {"type": "message", "text": "..."}
  ],
  "policy_sections_consulted": ["..."],
  "uncertainties": ["..."],
  "human_followup": "what a human still needs to do, if anything"
}

If you are uncertain about anything, say so. If a case needs human review, escalate it.
Never guess at booking references, amounts, or flight numbers. Use only what is in the data provided.
"""


def build_case_prompt(
    email_text: str,
    booking: dict[str, Any],
    flight: dict[str, Any] | None,
    entitlement: dict[str, Any] | None,
    customer: dict[str, Any] | None,
    policy_sections: str,
    availability: dict[str, Any] | None = None,
    hotel_allocation: dict[str, Any] | None = None,
    disruption_feed: dict[str, Any] | None = None,
) -> str:
    parts = [
        "=== INBOUND PASSENGER MESSAGE ===",
        email_text,
        "",
        "=== BOOKING RECORD ===",
        _format_booking(booking),
        "",
    ]

    if flight:
        parts.extend(["=== FLIGHT RECORD ===", _json(flight), ""])

    if entitlement:
        parts.extend(["=== ENTITLEMENT CALCULATION (authoritative) ===", _json(entitlement), ""])

    if customer:
        parts.extend(["=== CUSTOMER HISTORY ===", _json(customer), ""])

    if availability:
        # Limit to first 10 options to save tokens
        results = availability.get("results", [])[:10]
        avail_summary = {
            "total_results": availability.get("total_results", 0),
            "showing": len(results),
            "options": [
                {
                    "option_id": r.get("option_id"),
                    "flight_no": r.get("flight_no"),
                    "departure_local": r.get("departure_local"),
                    "arrival_local": r.get("arrival_local"),
                    "cabin": r.get("cabin"),
                    "seats_available": r.get("seats_available"),
                    "fare_gbp": r.get("fare_gbp", 0.0),
                }
                for r in results
            ],
        }
        parts.extend(["=== AVAILABLE FLIGHTS (pick one for rebooking) ===", _json(avail_summary), ""])

    if hotel_allocation:
        parts.extend(["=== HOTEL ALLOCATION ===", _json(hotel_allocation), ""])

    if disruption_feed:
        parts.extend(["=== NETWORK DISRUPTION FEED (relevant events only) ===", _json(disruption_feed), ""])

    if policy_sections:
        parts.extend(["=== RELEVANT POLICY SECTIONS ===", policy_sections, ""])

    parts.append("Based on the above, decide what to do. Output your decision as JSON.")
    return "\n".join(parts)


def _format_booking(booking: dict[str, Any]) -> str:
    lines = []
    lines.append(f"Reference: {booking.get('booking_ref')}")
    lines.append(f"Customer: {booking.get('customer_id')}")
    lines.append(f"Email: {booking.get('contact_email')}")
    lines.append(f"Tier: {booking.get('tier')}")
    lines.append(f"Total paid: £{booking.get('total_paid_gbp', 0):.2f}")
    if booking.get("special_requests"):
        lines.append(f"Special requests: {booking['special_requests']}")
    lines.append("Passengers:")
    for p in booking.get("passengers", []):
        lines.append(f"  - {p['passenger_id']}: {p['given_name']} {p['surname']} ({p['passenger_type']}, age {p['age']}) cabin_booked={p.get('cabin_booked')} cabin_flown={p.get('cabin_flown')} assistance={p.get('assistance')}")
    lines.append("Segments:")
    for s in booking.get("segments", []):
        lines.append(f"  - {s['segment_id']}: {s['flight_no']} {s['date']} {s['origin']}-{s['destination']} fare=£{s.get('segment_fare_gbp', 0):.2f} cabin={s.get('cabin')} affected={s.get('is_affected')}")
    lines.append(f"Final destination: {booking.get('final_destination')}")
    d = booking.get("disruption")
    if d:
        lines.append(f"Disruption: affected_segment={d.get('affected_segment')} rerouted_onto={d.get('rerouted_onto')} arrival_delay={d.get('arrival_delay_minutes')}min informed_days_before={d.get('informed_days_before')}")
    else:
        lines.append("Disruption: none recorded")
    return "\n".join(lines)


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)
