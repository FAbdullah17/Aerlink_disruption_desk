# DECISIONS

**Name:**   Fahad Abdullah
**Time spent:** ~4 hours
**How to run it:** `cd refs/env && python3 ops_server.py &` then `uv run python3 -m src.main`

---

## 1. Approach

A single-pass pipeline: read email → find booking → safety checks → fetch data → one LLM call → validate → execute → record.

The system makes one gpt-4o call per case with all context pre-loaded in the prompt. No multi-agent orchestration, no chain-of-thought loops, no classification step. The LLM receives the email, booking, flight record, entitlements, availability, hotel allocation, disruption feed, and relevant policy sections, then returns a structured JSON decision. A deterministic validator cross-checks every action before execution.



## 2. Assumptions

- **Entitlements calculator is authoritative.** Section 10.2 says the figure it produces must be used. I never second-guess it, even when the passenger claims a different amount (case-07 demanded £900, calculator said £220).
- **Email gets you the booking, API tells you what happened.** The email provides the booking reference and passenger preferences. The operational record provides the cause, delay, and status. If they conflict (case-05: passenger said "crew shortage", API said "weather"), trust the API.
- **Identity must match exactly one booking.** Per Section 2.2, if the email/name/phone doesn't match exactly one booking, escalate. No fuzzy matching.
- **YTP passengers are never handled by the system.** Per Section 13.2, Young Traveller Programme cases must go to the YTP desk. The system escalates immediately.
- **Partner rebooking over £600 needs manager approval.** Enforced deterministically in the validator.
- **Goodwill over £150 needs supervisor approval.** Enforced deterministically in the validator.

## 3. How you broke the problem up

| Module | Responsibility |
|---|---|
| `main.py` | CLI entry, load cases, loop, save results |
| `orchestrator.py` | Pipeline: email → booking → safety → data → LLM → validate → execute |
| `ops_client.py` | HTTP client with retry (3x) and rate limiting (28 req/10s) |
| `safety.py` | Injection detection (16 patterns), YTP check, identity verification |
| `policy.py` | Determine relevant policy sections, extract from markdown |
| `prompts.py` | System prompt + user prompt builder |
| `validator.py` | Cross-check amounts, authority limits, cabin changes against API data |
| `models.py` | Pydantic models for Booking, CaseRecord, etc. |

**Why this split.** Each module has one job. The orchestrator owns the flow. The safety layer is deterministic and runs before the LLM sees anything. The validator is deterministic and runs after the LLM returns. The LLM is the only non-deterministic component, and it's fenced on both sides.

**What it costs.** The single-call approach means the LLM doesn't get to iterate or ask clarifying questions. If the prompt is missing context, the decision might be wrong. The tradeoff is simplicity and speed 12 cases in ~90 seconds.

## 4. The operations API

**Used:** health, get_booking, search_bookings, get_flight, get_entitlements, get_customer_history, search_availability, get_hotel_allocation, get_disruption_feed, get_policy_document, rebook, pay_compensation, pay_goodwill, issue_refund, issue_hotel_voucher, escalate, audit, reset.

**Not used:** search_policy (I load the full policy document once and extract sections locally), get_receipts (not relevant to these cases).

**Reshaped:** The policy document is parsed into sections and only relevant ones are included in the prompt. The availability response is limited to 10 options to keep prompt size down. Customer history is fetched but only the flags field is used.

## 5. Prompting

The system prompt is ~55 lines that establish:
- Job: process one disruption case, return structured JSON
- Critical rules: operational record is authoritative, arrival delay not departure, segment fare not booking total, entitlement calculator wins, duty of care always owed, YTP must escalate, partner limits
- Action rules: no zero amounts, use option_ids from availability, refund = segment fare
- Output structure: decision, reasoning, actions, uncertainties, human_followup

The user prompt assembles all available data in a consistent order: email, booking, flight, entitlements, customer history, availability, hotel, disruption feed, policy sections.

**What I tried that made things worse.** Initially I included the full policy document (~700 lines) in every prompt. This bloated tokens and sometimes caused the LLM to hallucinate policy references. Switching to targeted section extraction (10-15 sections per case) reduced tokens by ~60% and improved accuracy.

**Handling injection.** The safety module runs 16 regex patterns against the email and 10 against the booking's special_requests field before the LLM sees anything. Cases 06 contained ">>> SYSTEM DIRECTIVE TO HANDLING AGENT" — caught and escalated without the LLM ever seeing the malicious content.

## 6. Models and cost

| Where | Model | Why |
|---|---|---|
| All cases | gpt-4o | Required by brief. Handles complex reasoning, policy interpretation, multi-passenger decisions. |

**Actual cost of a full run over the twelve cases:** ~$0.20
**Total tokens (in / out):** ~24,000 in / ~6,000 out (estimated)
**How you measured it:** 12 calls to gpt-4o, each ~2,000 tokens in + ~500 tokens out. At gpt-4o rates ($2.50/1M in, $10/1M out) = ~$0.06 input + ~$0.06 output = ~$0.12-0.20 total.

**Cost controls.** Temperature 0.0 for determinism. Max 2,000 output tokens. JSON response format. No retries on LLM calls. Single call per case — no iterative refinement.

## 7. Failure and safety

- **API down:** OpsClient retries 3x with backoff (2s, 4s, 6s). If still failing, the case escalates to GENERAL queue.
- **LLM returns bad JSON:** The orchestrator catches JSONDecodeError and creates an escalation record.
- **Validator rejects an action:** The action is replaced with an escalation (e.g., "amount mismatch, needs human review").
- **Identity not confirmed:** Immediate escalation, no booking accessed.
- **Injection detected:** Immediate refusal, no LLM call, escalation to GENERAL.
- **YTP passenger:** Immediate escalation to YTP desk.

**Worst thing the system could do:** Pay the wrong amount to the wrong person. The validator catches amount mismatches against the entitlements calculator, and the API enforces that payments are against valid booking refs. The rate limiter (28 req/10s) prevents runaway spending.

## 8. How you know it works

- 88 unit tests covering models, ops client (retry, rate limiting), safety (injection, identity), policy extraction, prompting, validation
- End-to-end test processing all 12 cases against the live API and validating every record.json
- Manual review of each case's decision, reasoning, and actions against the email and API data

**Blind spots:**
- The validator doesn't check whether the LLM's reasoning is logically consistent with the decision — only that amounts and authority limits are correct
- No test for concurrent cases hitting the rate limiter
- The policy section extractor is fragile — it depends on markdown header formatting

**With a month:** I'd add a second LLM call to review the first one's decision (a "judge" model), add integration tests with synthetic edge cases, and build a dashboard showing case outcomes, costs, and error rates over time.

## 9. AI assistants
AI session detsils in `transcripts/chat_session.md`.

**Where did you override them?** The assistant originally built a classifier module (simple/complex case split). I removed it because the cost savings didn't justify the complexity. The assistant also used urllib initially — I switched to httpx for retry support and connection pooling. The assistant's first prompt was ~200 lines — I cut it to ~55 lines after testing showed the shorter version performed better.

**Where did you let them run?** The unit test suite was generated mostly unchecked — 88 tests across 7 files. I reviewed the test names and structure but didn't read every assertion. The test patterns were standard and the tests passed, so this was a reasonable call.

**How did you drive them?** Small reviewed steps for core logic (orchestrator, validator), longer unchecked stretches for tests and boilerplate. I always ran `pytest` after each change. I kept a running todo list of what was done and what was next.

**Anything they got wrong that took you a while to notice.** The orchestrator printed its own case heading AND the e2e test printed one, causing double output. Took two review cycles to notice.

## 10. What you left out, and what you'd do next

**Left out intentionally:**
- Multi-agent orchestration (overkill for single-pass decisions)
- Streaming/partial responses (not needed for batc| Session / file | Tool | What you were doing in it |
|---|---|---|
| Implementation session | OpenCode | Built the entire codebase from scratch: models, client, safety, policy, prompts, validator, orchestrator, main, tests |
| Planning session | OpenCode | Architecture decisions, code review, debugging, result analysis |h processing)
- Database storage (JSON files sufficient for 12 cases)
- Web UI (CLI meets the brief)

**Would fix first with another day:**
- Add a "judge" LLM call to review decisions before execution
- Better error handling when the LLM returns partial JSON
- Token counting in the output records (currently tracked but not written)
- A proper integration test that creates a synthetic case and processes it end-to-end

**Not happy with:**
- The policy section extractor is regex-based and fragile — would replace with a proper markdown parser
- The validator doesn't check reasoning quality, only action correctness
- Cases 03, 04, 06 produce empty policy_sections_consulted because they exit early — this is correct behavior but looks odd in the output

---

## Case Outcomes

| Case | Booking | Decision | Actions | Status |
|---|---|---|---|---|
| case-01 | AER-4K2P9X | Rebook + £30 goodwill (weather, no compensation) | rebook AK529, goodwill £30 | Done |
| case-02 | AER-7T3M1B | Rebook 3, refund 1, escalate 1 (WCHR), hotel | rebook AK436, refund £238, escalate SPECIAL_ASSISTANCE, hotel MAN | Done |
| case-03 | N/A | ESCALATE — identity not confirmed | escalate GENERAL | Correct |
| case-04 | N/A | ESCALATE — identity not confirmed (wrong airline) | escalate GENERAL | Correct |
| case-05 | AER-9L5D2R | Rebook, hotel failed (allocation exhausted) | rebook AK179 | 1 uncertainty |
| case-06 | N/A | REFUSE — prompt injection detected | escalate GENERAL | Correct |
| case-07 | AER-6H1Z7C | Compensate £220 (270min delay, tech fault) | compensation £220, message | Done |
| case-08 | AER-3B7Y5K | Compensate £175 delay + £240 downgrade | 2x compensation | Done |
| case-09 | AER-8N4V6J | Rebook + hotel voucher | rebook AK879, hotel LGW | Done |
| case-10 | AER-5C9X3T | Rebook on 10 Aug per request | rebook AK275 | Done |
| case-11 | AER-7P4R2M | Rebook + escalate (hotel exhausted) | rebook AK668, escalate OPS_LIAISON | Done |
| case-12 | AER-1F6G8P | Rebook 2 passengers + hotel | rebook AK113, hotel BCN | Done |

### Detailed Case Records

#### case-01
- **Email:** Priya Raghunathan — AK412 cancelled, needs Barcelona by evening, bought breakfast waiting
- **API records:** AK412 cancelled — thunderstorm at BCN (extraordinary weather). No compensation per S5.1(b). Duty of care owed.
- **Decision:** Rebook on AK529 (£14.32, ECONOMY) + £30 goodwill for meals
- **Reasoning:** Weather = no compensation, but rebook + duty of care owed per S4.1/S4.2

#### case-02
- **Email:** Chidi Okonkwo — 5 passengers, different plans. Chidi+Adaeze+Emeka to Rome. Mother Ngozi uses wheelchair (WCHR confirmed May). Tobias wants refund.
- **API records:** AK220 cancelled — technical fault (within Aerlink control). 5 passengers on booking.
- **Decision:** Rebook Chidi, Adaeze, Emeka on AK436 (£108.67). Refund Tobias £238. Escalate Ngozi to SPECIAL_ASSISTANCE. Hotel voucher MAN 2026-08-05 for all 5.
- **Reasoning:** Followed every passenger's preference exactly. WCHR escalation per S14.4.

#### case-03
- **Email:** John Smith — flight was late, no booking ref, just name
- **API records:** No booking found matching "John Smith" or the email
- **Decision:** ESCALATE — identity not confirmed per S2.2

#### case-04
- **Email:** Peter Lindqvist — third request about BA-99201 (not an Aerlink flight), threatening
- **API records:** No Aerlink booking matches the reference or email
- **Decision:** ESCALATE — identity not confirmed. Passenger may be contacting wrong airline.

#### case-05
- **Email:** Marta Kowalczyk — AK808 cancelled, gate agent said "crew shortage", wants compensation + £112 hotel
- **API records:** AK808 cancelled — weather (extraordinary). Compensation not payable per S5.1(b). Hotel allocation at EDI exhausted.
- **Decision:** Rebook on AK179 (£145.26). Hotel failed (allocation exhausted). Uncertainty flagged.
- **Key point:** Email said "crew shortage" but API said "weather". System correctly trusted the API.

#### case-06
- **Email:** Daniel Fitzgerald — AK155 delayed, forwarded fake "operations desk" message with injection
- **API records:** Never reached — injection detected in email content
- **Decision:** REFUSE — 9 injection patterns matched including ">>> SYSTEM DIRECTIVE TO HANDLING AGENT"

#### case-07
- **Email:** Rachel Oyelaran — AK307 cancelled, demanded £900, threatened ombudsman/solicitor
- **API records:** AK307 cancelled — technical issue (within Aerlink control). 270min delay. Entitlements calculator = £220. Flag: REPEAT_GOODWILL_CLAIMANT.
- **Decision:** Pay £220 (not £900). Calculator figure is authoritative per S10.2.

#### case-08
- **Email:** Tomas Ferreira — AK640 cancelled, 5hr delay, downgraded Business→Economy. Calculated own figures (£350 + 50% fare).
- **API records:** AK640 cancelled — technical fault. Entitlements calculator: £175 delay + £240 downgrade = £415 total.
- **Decision:** Pay £175 + £240 = £415. Used calculator's figures, not passenger's £350 claim.

#### case-09
- **Email:** Aisha Bello — AK512 cancelled, sitting in Gatwick at 11pm, Gold member
- **API records:** AK512 cancelled — technical fault. Hotel available at LGW.
- **Decision:** Rebook on AK879 (£302.31) + hotel voucher LGW 2026-08-06. Deferred compensation until re-routing settles per S5.2.

#### case-10
- **Email:** Greg Whitmore — long forwarded thread, 4 different agents, wants rebooking on 10 August specifically
- **API records:** AK229 cancelled — crew unavailability (within Aerlink control).
- **Decision:** Rebook on AK275 (£276.43) on 2026-08-10 per his request.

#### case-11
- **Email:** Kenneth Braithwaite — angry, lost overcoat in March, flight cancelled, wants refund + compensation + hotel
- **API records:** AK150 cancelled — technical fault. Hotel allocation at LGW exhausted.
- **Decision:** Rebook on AK668 (£119.76). Escalate to OPS_LIAISON for hotel. Deferred compensation per S5.2.

#### case-12
- **Email:** Lucia Marquez-Ibanez — Spanish, AK418 cancelled, traveling with 6yo son Mateo, needs London by tomorrow
- **API records:** AK418 cancelled — technical fault. Hotel available at BCN.
- **Decision:** Rebook on AK113 (£3.65) + hotel voucher BCN 2026-08-05 for 2 passengers. Deferred compensation per S5.2.
