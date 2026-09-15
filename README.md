# Aerlink Disruption Desk

Automated case processor for airline disruption handling. Reads passenger emails, identifies bookings, checks flight records, calculates entitlements under the Passenger Care Policy, and takes appropriate actions (rebook, compensate, refund, escalate) using a single GPT-4o model call per case.

## Setup

```bash
# Install dependencies (uses uv)
uv sync

# Copy environment config and add your OpenAI key
cp .env.example .env
# Edit .env and set OPENAI_API_KEY
```

Requires Python 3.11+, an OpenAI API key, and the mock operations API.

## Running

```bash
# Start the operations API (separate terminal)
cd refs/env && python3 ops_server.py

# Run all 12 cases
uv run python -m src.main

# Run a specific case
uv run python -m src.main --case case-01

# Reset API state before running
uv run python -m src.main --reset
```

After processing, results are written to `results/`:

- `results/case-XX.json` — copy of each case record
- `results/summary.json` — array of `{case_id, decision, booking_ref, actions_count, status}`

## Testing

```bash
# Unit tests (88 tests)
uv run pytest

# End-to-end test (all 12 cases, calls OpenAI)
python3 tests/e2e_all_cases.py

# Single case e2e
python3 tests/e2e_all_cases.py --case case-03
```

## Architecture

```
src/
  main.py           CLI entry point, results/ output
  ops_client.py     httpx client with retry + rate limiting (30 req/10s)
  safety.py         Pre-LLM checks (YTP, injection, identity)
  policy.py         Targeted policy section extraction
  prompts.py        LLM prompt builder (single gpt-4o call)
  validator.py      Cross-check LLM decisions vs API data
  orchestrator.py   End-to-end case processing pipeline
  models.py         Pydantic v2 models

tests/
  test_models.py          Model validation
  test_safety.py          Injection, identity, YTP, run_safety_checks pipeline
  test_validator.py       Action validation, hotel vouchers, escalation replacement
  test_policy.py          Section selection, multi-passenger, extraordinary causes
  test_ops_client.py      Retry on 503/429/connection, rate limiter
  test_prompts.py         Prompt building, format_booking
  e2e_all_cases.py        Full 12-case end-to-end test

refs/
  env/              Mock operations API server + data
  cases/            12 test cases with inbound emails and expected records
```

## How It Works

1. **Parse email** — extract booking reference, sender email, name
2. **Search booking** — find the booking via API (by ref, email, or name)
3. **Safety checks** — YTP detection, injection pattern matching, identity verification (all deterministic, before LLM)
4. **Fetch data** — flight record, entitlements, customer history, availability, hotel allocation, disruption feed
5. **Call LLM** — single GPT-4o call with all context, returns structured JSON decision
6. **Validate** — cross-check every proposed action against API data (amounts, authority limits, policy)
7. **Execute** — only actions that pass validation are executed
8. **Record** — write `record.json` with decision, reasoning, actions, uncertainties

## Safety Layers

- **YTP passengers** → escalated immediately, model never sees the case
- **Prompt injection** → 20+ regex patterns detect fake system directives in emails and special_requests
- **Identity verification** — must match exactly one booking per Section 2.2
- **Validator** — cross-checks every payment against entitlement calculator; catches amount mismatches, unauthorized actions, cabin changes
- **Authority limits** — partner rebooking >£600/manager, goodwill >£150/supervisor enforced deterministically

## Case Outcomes Summary

| Case | Outcome | Actions |
|------|---------|---------|
| case-01 | Rebook + meal reimbursement | rebook, goodwill |
| case-02 | Rebook 3, refund 1, escalate 1 (SA), hotel | rebook, refund, escalate, hotel |
| case-03 | Escalate — identity not confirmed | escalate |
| case-04 | Escalate — identity not confirmed | escalate |
| case-05 | Rebook + hotel voucher | rebook, hotel_voucher |
| case-06 | Refuse — injection detected | escalate |
| case-07 | Compensate £220 | compensation, message |
| case-08 | Compensate £175 + £240 downgrade | compensation x2 |
| case-09 | Rebook + hotel voucher | rebook, hotel_voucher |
| case-10 | Rebook on Monday | rebook |
| case-11 | Rebook + hotel voucher | rebook, escalate |
| case-12 | Rebook + hotel voucher | rebook, hotel_voucher |

## Adding a New Case

Place your case in `refs/cases/case-XX/` with:

- `inbound.txt` — the passenger email
- `meta.json` — metadata:

```json
{
  "case_id": "case-XX",
  "channel": "email",
  "received_at": "2026-08-07T06:20:33Z",
  "from": "Passenger Name <email@example.com>",
  "subject": "Subject line"
}
```

Then run: `uv run python -m src.main --case case-XX`
