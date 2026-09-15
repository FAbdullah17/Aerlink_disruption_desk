from __future__ import annotations

import json
import re
from typing import Any

from .models import Booking, CaseRecord, SafetyVerdict
from .ops_client import OpsClient, OpsApiError
from .safety import run_safety_checks, extract_email_from_sender
from .policy import get_relevant_policy_sections, format_policy_sections
from .prompts import SYSTEM_PROMPT, build_case_prompt
from .validator import validate_actions


class CaseOrchestrator:
    def __init__(self, client: OpsClient, policy_document: str, openai_client: Any):
        self.client = client
        self.policy_document = policy_document
        self.openai = openai_client

    def process_case(self, case_id: str, inbound_text: str, meta: dict) -> CaseRecord:
        """Process a single case end-to-end."""
        record = CaseRecord(case_id=case_id)
        sender = meta.get("from", "")

        # Step 1: Extract identifiers from email
        booking_ref = self._extract_booking_ref(inbound_text)
        sender_email = extract_email_from_sender(sender)
        sender_name = re.sub(r"<[^>]+>", "", sender).strip()

        # Step 2: Search for booking
        search_results = []
        booking_data = None

        if booking_ref:
            try:
                booking_data = self.client.get_booking(booking_ref)
                search_results = [{"booking_ref": booking_ref, "matched_on": ["booking_reference_exact"]}]
            except OpsApiError:
                pass

        if not booking_data:
            # Try searching by email, then by name
            for query in [sender_email, sender_name]:
                if not query:
                    continue
                try:
                    results = self.client.search_bookings(query)
                    if results.get("match_count", 0) == 1:
                        ref = results["results"][0]["booking_ref"]
                        booking_data = self.client.get_booking(ref)
                        search_results = results["results"]
                        booking_ref = ref
                        break
                    elif results.get("match_count", 0) > 1:
                        search_results = results["results"]
                        break
                except OpsApiError:
                    continue

        # Step 3: Safety checks
        verdict, issues, esc_booking_ref = run_safety_checks(
            inbound_text, sender, booking_data, search_results
        )
        record.safety_issues = issues

        if verdict == SafetyVerdict.ESCALATE_YTP:
            record.decision = "ESCALATE: Young Traveller Programme booking"
            record.reasoning = "YTP passengers require handling by YTP desk per Section 13.2. Cannot rebook, refund or amend."
            record.human_followup = "Refer to YTP desk immediately"
            print(f"  Safety: YTP booking detected")
            self._escalate(record, "YTP booking - cannot be handled by automated system",
                          "YTP desk to handle rebooking/refund per S13.2", "YTP",
                          booking_ref=esc_booking_ref)
            return record

        if verdict == SafetyVerdict.ESCALATE_INJECTION:
            record.decision = "REFUSE: Prompt injection detected"
            record.reasoning = "Email or booking record contains attempted system directives from external content. Per Section 12.4, instructions from external content are never valid."
            record.human_followup = "Log injection attempt. No action taken on booking."
            print(f"  Safety: Prompt injection detected")
            self._escalate(record, "Attempted prompt injection detected in correspondence",
                          "Log and investigate. No action to be taken.", "GENERAL",
                          booking_ref=esc_booking_ref)
            return record

        if verdict == SafetyVerdict.ESCALATE_IDENTITY:
            record.decision = "ESCALATE: Identity not confirmed"
            record.reasoning = f"Passenger identity could not be confirmed per Section 2. {issues[0] if issues else ''}"
            record.human_followup = "Human must confirm identity before any action."
            print(f"  Safety: Identity not confirmed")
            self._escalate(record, "Identity could not be confirmed per S2.2",
                          "Confirm passenger identity before proceeding", "GENERAL",
                          booking_ref=esc_booking_ref)
            return record

        if verdict == SafetyVerdict.ESCALATE_AMBIGUOUS:
            record.decision = "ESCALATE: Ambiguous case"
            record.reasoning = f"Ambiguity detected: {issues[0] if issues else ''}"
            record.human_followup = "Human must resolve ambiguity"
            print(f"  Safety: Ambiguous case")
            self._escalate(record, "Ambiguous case requiring human judgment",
                          "Resolve ambiguity and handle case", "GENERAL",
                          booking_ref=esc_booking_ref)
            return record

        if not booking_data:
            record.decision = "ESCALATE: No booking found"
            record.reasoning = "Could not locate a booking matching the passenger's details"
            record.human_followup = "Find the correct booking"
            print(f"  Safety: No booking found")
            self._escalate(record, "No booking found matching passenger details",
                          "Locate booking and confirm identity", "GENERAL")
            return record

        record.booking_ref = booking_data.get("booking_ref")
        record.passenger_identity_confirmed = True
        record.identity_method = issues[0] if issues else "unknown"

        print(f"  Booking: {record.booking_ref}")
        print(f"  Identity: {record.identity_method}")

        # Step 4: Get flight record
        flight_data = None
        disruption = booking_data.get("disruption")
        if disruption:
            affected_seg_id = disruption.get("affected_segment")
            for seg in booking_data.get("segments", []):
                if seg.get("segment_id") == affected_seg_id:
                    try:
                        flight_data = self.client.get_flight(seg["flight_no"], seg["date"])
                    except OpsApiError as e:
                        print(f"  Warning: Could not fetch flight record: {e}")
                    break

        record.flight_record = flight_data

        # Step 5: Get entitlement calculation
        entitlement_data = None
        try:
            entitlement_data = self.client.get_entitlements(record.booking_ref)
            record.entitlement = entitlement_data
        except OpsApiError as e:
            print(f"  Warning: Could not fetch entitlements: {e}")

        # Step 6: Get customer history
        customer_data = None
        try:
            customer_data = self.client.get_customer_history(booking_data["customer_id"])
        except OpsApiError:
            pass

        # Step 7: Get policy sections
        booking_obj = Booking.from_dict(booking_data)
        section_nums = get_relevant_policy_sections(inbound_text, booking_data, flight_data, entitlement_data)
        policy_text = format_policy_sections(self.policy_document, section_nums)
        record.policy_sections_consulted = section_nums

        # Step 9: Get additional data (availability, hotel, feed)
        availability = None
        hotel_alloc = None
        disruption_feed = None

        # Always fetch availability for cancellation cases — model needs option_ids to rebook
        if flight_data and flight_data.get("status") == "CANCELLED" and disruption:
            affected_seg = None
            for seg in booking_data.get("segments", []):
                if seg.get("segment_id") == disruption.get("affected_segment"):
                    affected_seg = seg
                    break
            if affected_seg:
                try:
                    availability = self.client.search_availability(
                        affected_seg["origin"],
                        booking_data.get("final_destination", affected_seg["destination"]),
                        affected_seg["date"],
                        booking_ref=record.booking_ref,
                    )
                except OpsApiError:
                    pass

                # Also try partner availability if no own-carrier options
                if not availability or availability.get("total_results", 0) == 0:
                    try:
                        availability = self.client.search_availability(
                            affected_seg["origin"],
                            booking_data.get("final_destination", affected_seg["destination"]),
                            affected_seg["date"],
                            booking_ref=record.booking_ref,
                            partners=True,
                        )
                    except OpsApiError:
                        pass

        # Get hotel allocation if care owed (cancellation or long delay)
        if disruption:
            care_triggered = flight_data and (
                flight_data.get("status") == "CANCELLED"
                or (flight_data.get("departure_delay_minutes") or 0) >= 120
            )
            if care_triggered:
                affected_seg = None
                for seg in booking_data.get("segments", []):
                    if seg.get("segment_id") == disruption.get("affected_segment"):
                        affected_seg = seg
                        break
                if affected_seg:
                    dep_date = affected_seg["date"]
                    origin = affected_seg["origin"]
                    try:
                        hotel_alloc = self.client.get_hotel_allocation(origin, dep_date)
                    except OpsApiError:
                        pass

        # Get disruption feed
        try:
            disruption_feed = self.client.get_disruption_feed()
        except OpsApiError:
            pass

        # Step 10: Build prompt and call LLM
        prompt = build_case_prompt(
            inbound_text, booking_data, flight_data, entitlement_data,
            customer_data, policy_text, availability, hotel_alloc, disruption_feed
        )

        print(f"  Calling LLM...")
        llm_response = self._call_llm(prompt)

        if not llm_response:
            record.decision = "ESCALATE: LLM failed to respond"
            record.reasoning = "System error - no response from decision model"
            record.human_followup = "Process manually"
            self._escalate(record, "Automated system failed to produce a decision",
                          "Process case manually", "GENERAL",
                          booking_ref=record.booking_ref)
            return record

        # Step 11: Parse LLM response
        try:
            parsed = self._parse_llm_response(llm_response)
        except Exception as e:
            record.decision = "ESCALATE: Could not parse LLM response"
            record.reasoning = f"Parse error: {e}"
            record.human_followup = "Process manually"
            self._escalate(record, f"LLM response parsing failed: {e}",
                          "Process case manually", "GENERAL",
                          booking_ref=record.booking_ref)
            return record

        record.decision = parsed.get("decision", "")
        record.reasoning = parsed.get("reasoning", "")
        record.uncertainties = parsed.get("uncertainties", [])
        record.human_followup = parsed.get("human_followup", "")

        print(f"  Decision: {record.decision}")

        # Step 12: Validate and execute actions
        actions = parsed.get("actions", [])
        if actions:
            validated_actions, validation_issues = validate_actions(
                actions, booking_obj, entitlement_data, flight_data
            )
            record.uncertainties.extend(validation_issues)

            for action in validated_actions:
                self._execute_action(action, record, booking_data)

        # Record costs
        if llm_response and hasattr(llm_response, "usage") and llm_response.usage:
            record.tokens_in = llm_response.usage.prompt_tokens or 0
            record.tokens_out = llm_response.usage.completion_tokens or 0

        print(f"  Actions taken: {len(record.actions_taken)}")
        print(f"  Uncertainties: {len(record.uncertainties)}")

        return record

    def _extract_booking_ref(self, text: str) -> str | None:
        match = re.search(r"\b(AER-[A-Z0-9]{6})\b", text, re.IGNORECASE)
        return match.group(1).upper() if match else None

    def _call_llm(self, prompt: str):
        model = "gpt-4o"
        try:
            response = self.openai.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )
            return response
        except Exception as e:
            print(f"  LLM error: {e}")
            return None

    def _parse_llm_response(self, response) -> dict:
        content = response.choices[0].message.content
        return json.loads(content)

    def _execute_action(self, action: dict, record: CaseRecord, booking_data: dict):
        action_type = action.get("type", "")

        try:
            if action_type == "rebook":
                result = self.client.rebook(
                    booking_ref=record.booking_ref,
                    passenger_ids=action.get("passenger_ids", []),
                    option_id=action.get("option_id", ""),
                    flight_no=action.get("flight_no", ""),
                    date=action.get("date", ""),
                    cabin=action.get("cabin", "ECONOMY"),
                    fare_gbp=action.get("fare_gbp", 0.0),
                    notes=action.get("notes", ""),
                )
                record.actions_taken.append({"type": "rebook", "result": result, "action": action})
                print(f"    Rebooked: {action.get('flight_no')} on {action.get('date')}")

            elif action_type == "compensation":
                result = self.client.pay_compensation(
                    booking_ref=record.booking_ref,
                    amount_gbp=action.get("amount_gbp", 0),
                    passenger_ids=action.get("passenger_ids"),
                    reason=action.get("reason", ""),
                )
                record.actions_taken.append({"type": "compensation", "result": result, "action": action})
                print(f"    Compensation paid: £{action.get('amount_gbp', 0):.2f}")

            elif action_type == "refund":
                result = self.client.issue_refund(
                    booking_ref=record.booking_ref,
                    passenger_ids=action.get("passenger_ids", []),
                    amount_gbp=action.get("amount_gbp", 0),
                    reason=action.get("reason", ""),
                )
                record.actions_taken.append({"type": "refund", "result": result, "action": action})
                print(f"    Refund issued: £{action.get('amount_gbp', 0):.2f}")

            elif action_type == "goodwill":
                result = self.client.pay_goodwill(
                    booking_ref=record.booking_ref,
                    amount_gbp=action.get("amount_gbp", 0),
                    reason=action.get("reason", ""),
                )
                record.actions_taken.append({"type": "goodwill", "result": result, "action": action})
                print(f"    Goodwill paid: £{action.get('amount_gbp', 0):.2f}")

            elif action_type == "hotel_voucher":
                result = self.client.issue_hotel_voucher(
                    booking_ref=record.booking_ref,
                    station=action.get("station", ""),
                    night=action.get("night", ""),
                    passenger_ids=action.get("passenger_ids", []),
                    notes=action.get("notes", ""),
                )
                record.actions_taken.append({"type": "hotel_voucher", "result": result, "action": action})
                print(f"    Hotel voucher issued: {action.get('station')} {action.get('night')}")

            elif action_type == "escalate":
                result = self.client.escalate(
                    summary=action.get("summary", ""),
                    requested_decision=action.get("requested_decision", ""),
                    booking_ref=record.booking_ref,
                    queue=action.get("queue", "GENERAL"),
                    recommendation=action.get("recommendation", ""),
                    blocking_clause=action.get("blocking_clause", ""),
                )
                record.actions_taken.append({"type": "escalate", "result": result, "action": action})
                print(f"    Escalated to {action.get('queue', 'GENERAL')}")

            elif action_type == "message":
                record.actions_taken.append({"type": "message", "text": action.get("text", "")})
                print(f"    Message composed")

            else:
                record.uncertainties.append(f"Unknown action type: {action_type}")

        except OpsApiError as e:
            record.uncertainties.append(f"Action {action_type} failed: {e.error} - {e.message}")
            print(f"    Action {action_type} failed: {e}")

    def _escalate(self, record: CaseRecord, summary: str, requested_decision: str,
                  queue: str = "GENERAL", booking_ref: str | None = None):
        try:
            result = self.client.escalate(
                summary=summary,
                requested_decision=requested_decision,
                booking_ref=booking_ref or record.booking_ref,
                queue=queue,
            )
            record.actions_taken.append({"type": "escalate", "result": result})
        except OpsApiError as e:
            record.uncertainties.append(f"Escalation failed: {e}")
