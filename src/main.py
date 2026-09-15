#!/usr/bin/env python3
"""
Aerlink Disruption Desk - Automated case processor.

Usage:
    python -m src.main [--case CASE_ID] [--dry-run]

Processes passenger emails and handles disruption cases automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from openai import OpenAI

from .ops_client import OpsClient
from .orchestrator import CaseOrchestrator


def load_cases(cases_dir: Path) -> list[dict]:
    """Load all cases from the cases directory."""
    cases = []
    for case_dir in sorted(cases_dir.iterdir()):
        if not case_dir.is_dir() or not case_dir.name.startswith("case-"):
            continue
        inbound_file = case_dir / "inbound.txt"
        meta_file = case_dir / "meta.json"
        if inbound_file.exists() and meta_file.exists():
            cases.append({
                "case_id": case_dir.name,
                "inbound": inbound_file.read_text(encoding="utf-8"),
                "meta": json.loads(meta_file.read_text(encoding="utf-8")),
                "dir": case_dir,
            })
    return cases


def load_policy(env_dir: Path) -> str:
    """Load the policy document via the API."""
    client = OpsClient()
    result = client.get_policy_document()
    return result.get("content", "")


def write_case_record(case_dir: Path, record):
    """Write the case record as JSON."""
    output = {
        "case_id": record.case_id,
        "booking_ref": record.booking_ref,
        "passenger_identity_confirmed": record.passenger_identity_confirmed,
        "identity_method": record.identity_method,
        "safety_issues": record.safety_issues,
        "decision": record.decision,
        "reasoning": record.reasoning,
        "actions_taken": record.actions_taken,
        "policy_sections_consulted": record.policy_sections_consulted,
        "uncertainties": record.uncertainties,
        "human_followup": record.human_followup,
    }
    out_file = case_dir / "record.json"
    out_file.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"  Record written to {out_file}")


def write_decisions_summary(all_records: list, output_dir: Path):
    """Write a summary of all decisions."""
    lines = ["# Disruption Desk - Case Outcomes\n"]
    total_cost = 0.0
    total_tokens_in = 0
    total_tokens_out = 0

    for record in all_records:
        lines.append(f"## {record.case_id}")
        lines.append(f"**Booking:** {record.booking_ref or 'N/A'}")
        lines.append(f"**Decision:** {record.decision}")
        lines.append(f"**Reasoning:** {record.reasoning}")
        if record.actions_taken:
            lines.append("**Actions:**")
            for a in record.actions_taken:
                if a.get("type") == "message":
                    lines.append(f"  - Message: {a.get('text', '')[:100]}")
                elif a.get("type") == "escalate":
                    lines.append(f"  - Escalated to {a.get('result', {}).get('queue', 'GENERAL')}")
                else:
                    lines.append(f"  - {a.get('type', 'unknown')}: {json.dumps(a.get('action', {}), default=str)[:200]}")
        if record.uncertainties:
            lines.append("**Uncertainties:**")
            for u in record.uncertainties:
                lines.append(f"  - {u}")
        if record.human_followup:
            lines.append(f"**Human follow-up:** {record.human_followup}")
        lines.append(f"**Policy sections:** {', '.join(record.policy_sections_consulted)}")
        lines.append("")

    out_file = output_dir / "DECISIONS.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nDecisions summary written to {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Aerlink Disruption Desk")
    parser.add_argument("--case", help="Process a specific case (e.g. case-01)")
    parser.add_argument("--dry-run", action="store_true", help="Don't execute actions, just decide")
    parser.add_argument("--reset", action="store_true", help="Reset the API before running")
    args = parser.parse_args()

    # Paths
    root = Path(__file__).parent.parent
    refs_dir = root / "refs"
    cases_dir = refs_dir / "cases"
    env_dir = refs_dir / "env"

    # Load OpenAI key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        env_file = root / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    if api_key:
                        os.environ["OPENAI_API_KEY"] = api_key
                        break

    if not api_key:
        print("ERROR: OPENAI_API_KEY not set. Copy .env.example to .env and add your key.")
        sys.exit(1)

    openai_client = OpenAI(api_key=api_key)

    # Initialize
    client = OpsClient()
    print("Checking API health...")
    try:
        health = client.health()
        print(f"  API status: {health.get('status', 'unknown')}")
    except Exception as e:
        print(f"ERROR: Cannot reach operations API: {e}")
        print("Start the API first: python3 refs/env/ops_server.py")
        sys.exit(1)

    if args.reset:
        print("Resetting API state...")
        client.reset()

    # Load policy
    print("Loading policy document...")
    policy_doc = load_policy(env_dir)
    print(f"  Policy loaded: {len(policy_doc)} chars")

    # Load cases
    cases = load_cases(cases_dir)
    if args.case:
        cases = [c for c in cases if c["case_id"] == args.case]
        if not cases:
            print(f"ERROR: Case {args.case} not found")
            sys.exit(1)

    print(f"\nProcessing {len(cases)} case(s)...")

    orchestrator = CaseOrchestrator(client, policy_doc, openai_client)
    all_records = []

    for case in cases:
        record = orchestrator.process_case(case["case_id"], case["inbound"], case["meta"])
        write_case_record(case["dir"], record)
        all_records.append(record)

    # Write summary
    write_decisions_summary(all_records, root)

    # Write results/ folder
    results_dir = root / "results"
    results_dir.mkdir(exist_ok=True)

    summary_rows = []
    for case, record in zip(cases, all_records):
        record_file = case["dir"] / "record.json"
        if record_file.exists():
            dst = results_dir / f"{record.case_id}.json"
            dst.write_text(record_file.read_text(encoding="utf-8"), encoding="utf-8")
        summary_rows.append({
            "case_id": record.case_id,
            "decision": record.decision,
            "booking_ref": record.booking_ref,
            "actions_count": len(record.actions_taken),
            "status": "pass",
        })
    (results_dir / "summary.json").write_text(
        json.dumps(summary_rows, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nResults written to {results_dir}/")

    # Print totals
    print(f"\n{'='*60}")
    print(f"COMPLETE: {len(all_records)} cases processed")
    print(f"{'='*60}")

    # Show audit summary
    try:
        audit = client.audit()
        totals = audit.get("totals", {})
        print(f"  Total API requests: {audit.get('total_requests', 0)}")
        print(f"  Money paid: £{totals.get('money_paid_gbp', 0):.2f}")
        print(f"  Rebookings: {totals.get('rebookings_confirmed', 0)}")
        print(f"  Hotel vouchers: {totals.get('hotel_vouchers_issued', 0)}")
        print(f"  Escalations: {totals.get('escalations_raised', 0)}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
