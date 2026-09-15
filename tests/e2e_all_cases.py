#!/usr/bin/env python3
"""
End-to-end test for all 12 Aerlink disruption desk cases.

Starts the ops API server, processes every case through the orchestrator
(which calls OpenAI and the mock API), then validates each output record.

Usage:
    python3 tests/e2e_all_cases.py          # full run
    python3 tests/e2e_all_cases.py --case case-03   # single case
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "refs" / "cases"
RECORD_FIELDS = {
    "case_id",
    "booking_ref",
    "passenger_identity_confirmed",
    "identity_method",
    "safety_issues",
    "decision",
    "reasoning",
    "actions_taken",
    "policy_sections_consulted",
    "uncertainties",
    "human_followup",
}


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------

def wait_for_api(url: str = "http://127.0.0.1:8642/health", timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.3)
    return False


def start_server() -> subprocess.Popen | None:
    if wait_for_api(timeout=1.0):
        print("  [server] Already running on :8642")
        return None

    server_py = ROOT / "refs" / "env" / "ops_server.py"
    if not server_py.exists():
        print(f"  [server] ERROR: {server_py} not found")
        sys.exit(1)

    print("  [server] Starting ops_server.py ...")
    proc = subprocess.Popen(
        [sys.executable, str(server_py)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(server_py.parent),
    )
    if not wait_for_api(timeout=10.0):
        print("  [server] ERROR: server did not become healthy")
        proc.kill()
        sys.exit(1)
    print("  [server] Healthy")
    return proc


def reset_api():
    url = "http://127.0.0.1:8642/_reset"
    req = urllib.request.Request(url, method="POST", headers={"X-Ops-Key": "aerlink-ops-local-key"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Case processing
# ---------------------------------------------------------------------------

def load_cases(case_filter: str | None = None) -> list[dict]:
    cases = []
    for case_dir in sorted(CASES_DIR.iterdir()):
        if not case_dir.is_dir() or not case_dir.name.startswith("case-"):
            continue
        if case_filter and case_dir.name != case_filter:
            continue
        inbound = case_dir / "inbound.txt"
        meta = case_dir / "meta.json"
        if inbound.exists() and meta.exists():
            cases.append({
                "case_id": case_dir.name,
                "inbound": inbound.read_text(encoding="utf-8"),
                "meta": json.loads(meta.read_text(encoding="utf-8")),
                "dir": case_dir,
            })
    return cases


def process_all(cases: list[dict]) -> dict[str, dict]:
    """Run main.py logic in-process for each case. Returns {case_id: record_dict}."""
    sys.path.insert(0, str(ROOT))

    from openai import OpenAI
    from src.ops_client import OpsClient
    from src.orchestrator import CaseOrchestrator
    from src.main import load_policy, write_case_record

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    if api_key:
                        os.environ["OPENAI_API_KEY"] = api_key
                        break
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set")
        sys.exit(1)

    client = OpsClient()
    policy_doc = load_policy(ROOT / "refs" / "env")
    openai_client = OpenAI(api_key=api_key)
    orchestrator = CaseOrchestrator(client, policy_doc, openai_client)

    results = {}
    for case in cases:
        cid = case["case_id"]
        print(f"\n  [{cid}] Processing...")
        try:
            record = orchestrator.process_case(cid, case["inbound"], case["meta"])
            write_case_record(case["dir"], record)
            record_file = case["dir"] / "record.json"
            results[cid] = json.loads(record_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [{cid}] EXCEPTION: {e}")
            results[cid] = {"case_id": cid, "_error": str(e)}
    return results


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_record(case_id: str, record: dict) -> list[str]:
    """Validate a single record. Returns list of failure descriptions (empty = pass)."""
    errors = []

    if "_error" in record:
        errors.append(f"Processing failed: {record['_error']}")
        return errors

    # 1. Required fields
    missing = RECORD_FIELDS - set(record.keys())
    if missing:
        errors.append(f"Missing fields: {', '.join(sorted(missing))}")

    # 2. Decision must be non-empty
    decision = record.get("decision", "").strip()
    if not decision:
        errors.append("Decision is empty")

    # 3. actions_taken must be a list
    actions = record.get("actions_taken")
    if not isinstance(actions, list):
        errors.append(f"actions_taken is {type(actions).__name__}, expected list")
        actions = []

    # 4. Each action must have a type and be executed (have a result)
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            errors.append(f"Action {i}: is {type(action).__name__}, expected dict")
            continue
        if "type" not in action:
            errors.append(f"Action {i}: missing 'type'")
        if action.get("type") != "message" and "result" not in action:
            errors.append(f"Action {i} ({action.get('type', '?')}): missing 'result' (not executed)")

    # 5. If booking_ref present, must start with AER-
    booking_ref = record.get("booking_ref")
    if booking_ref is not None:
        if not isinstance(booking_ref, str) or not booking_ref.startswith("AER-"):
            errors.append(f"booking_ref '{booking_ref}' does not start with AER-")

    # 6. Policy sections consulted must be non-empty (skip for early exits like escalation)
    decision = record.get("decision", "")
    is_escalation = decision.startswith("ESCALATE") or decision.startswith("REFUSE")
    sections = record.get("policy_sections_consulted", [])
    if not is_escalation and (not isinstance(sections, list) or len(sections) == 0):
        errors.append("No policy sections consulted")

    return errors


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(results: dict[str, dict]):
    print(f"\n{'='*72}")
    print(f"  END-TO-END TEST RESULTS")
    print(f"{'='*72}")

    passed = []
    failed = []

    for cid in sorted(results.keys()):
        record = results[cid]
        errors = validate_record(cid, record)
        if errors:
            failed.append((cid, errors))
        else:
            passed.append(cid)

    # Print each case
    for cid in sorted(results.keys()):
        record = results[cid]
        errors = validate_record(cid, record)
        status = "PASS" if not errors else "FAIL"
        icon = "+" if status == "PASS" else "X"

        print(f"\n  [{icon}] {cid}: {status}")
        print(f"      Decision: {record.get('decision', 'N/A')[:80]}")
        print(f"      Booking:  {record.get('booking_ref', 'N/A')}")
        print(f"      Actions:  {len(record.get('actions_taken', []))}")

        action_summary = []
        for a in record.get("actions_taken", []):
            if not isinstance(a, dict):
                continue
            atype = a.get("type", "?")
            if atype == "rebook":
                action_summary.append(f"rebook->{a.get('result',{}).get('flight_no','?')}")
            elif atype in ("compensation", "refund", "goodwill"):
                amt = a.get("action", {}).get("amount_gbp", 0)
                action_summary.append(f"{atype}->£{amt:.2f}")
            elif atype == "hotel_voucher":
                action_summary.append(f"hotel->{a.get('action',{}).get('station','?')}")
            elif atype == "escalate":
                action_summary.append(f"escalate->{a.get('result',{}).get('queue','?')}")
            else:
                action_summary.append(atype)
        if action_summary:
            print(f"      Detail:   {', '.join(action_summary)}")

        if errors:
            for e in errors:
                print(f"      ERROR: {e}")

        if record.get("uncertainties"):
            print(f"      Warnings: {len(record['uncertainties'])}")

    # Summary box
    total = len(results)
    print(f"\n{'='*72}")
    print(f"  SUMMARY: {len(passed)}/{total} passed, {len(failed)}/{total} failed")
    print(f"{'='*72}")

    if failed:
        print(f"\n  Failed cases:")
        for cid, errors in failed:
            print(f"    {cid}:")
            for e in errors:
                print(f"      - {e}")

    return len(failed) == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="E2E test for all 12 cases")
    parser.add_argument("--case", help="Run a single case (e.g. case-03)")
    args = parser.parse_args()

    print("=" * 60)
    print("  AERLINK DISRUPTION DESK - END-TO-END TEST")
    print("=" * 60)

    server_proc = start_server()

    try:
        print("\n  [reset] Clearing API state...")
        if reset_api():
            print("  [reset] Done")
        else:
            print("  [reset] WARNING: reset may have failed")

        cases = load_cases(args.case)
        if not cases:
            print("ERROR: No cases found")
            sys.exit(1)
        print(f"\n  Found {len(cases)} case(s) to process")

        results = process_all(cases)

        all_pass = print_summary(results)

        # Write results/ folder with copies and summary
        results_dir = ROOT / "results"
        results_dir.mkdir(exist_ok=True)

        summary_rows = []
        for cid in sorted(results.keys()):
            record = results[cid]
            errors = validate_record(cid, record)

            # Copy record.json
            src = CASES_DIR / cid / "record.json"
            dst = results_dir / f"{cid}.json"
            if src.exists():
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

            summary_rows.append({
                "case_id": cid,
                "decision": record.get("decision", ""),
                "booking_ref": record.get("booking_ref"),
                "actions_count": len(record.get("actions_taken", [])),
                "status": "pass" if not errors else "fail",
            })

        (results_dir / "summary.json").write_text(
            json.dumps(summary_rows, indent=2, default=str), encoding="utf-8"
        )
        print(f"\n  Results written to {results_dir}/")

        sys.exit(0 if all_pass else 1)

    finally:
        if server_proc:
            print("\n  [server] Shutting down...")
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()


if __name__ == "__main__":
    main()
