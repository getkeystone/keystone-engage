"""Eval harness for Keystone Engage.

Runs eval cases against a live server. Results saved to data/eval/results/.
Run: uv run python -m keystone_engage.eval [base_url]

Cases carry a `bucket` (e.g. core-regression | edge-case) and optional
behavioral-content assertions (`expected_contains` / `expected_absent`) plus
a `pair_id` linking fairness-paired variants. After the case loop, three
post-run checks run:

  - fairness: cases sharing a `pair_id` must be treated equivalently
    (same pass/fail and same severity). Divergence is a fairness violation.
  - audit:    every response must carry a non-empty audit hash; if the audit
    ledger is local to the eval host, its hash chain is verified end to end.
  - cost:     total cost is surfaced from the audit ledger when persisted.

Exit code reflects case pass/fail only; fairness/audit/cost issues are
reported (and WARN-printed) but do not by themselves fail the run.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

EVAL_DATA_DIR = Path("data/eval")
EVAL_RESULTS_DIR = Path("data/eval/results")
AUDIT_LEDGER_PATH = Path("data/audit/ledger.jsonl")
DEFAULT_BASE_URL = "http://localhost:8100"


@dataclass
class EvalCase:
    case_id: str
    category: str
    input_message: str
    expected_behavior: str
    description: str = ""
    caller_id: str | None = None
    bucket: str = "core-regression"
    expected_contains: list[str] = field(default_factory=list)
    expected_absent: list[str] = field(default_factory=list)
    expected_contains_any: list[str] = field(default_factory=list)
    pair_id: str | None = None


@dataclass
class EvalResult:
    case_id: str
    category: str
    passed: bool
    expected_behavior: str
    actual_severity: str
    actual_fail_closed: bool
    response_length: int
    latency_ms: float
    details: str = ""
    bucket: str = "core-regression"
    pair_id: str | None = None
    audit_hash: str = ""
    content_ok: bool = True


@dataclass
class EvalRun:
    run_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    results: list[EvalResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    def summary(self) -> dict:
        by_category: dict[str, dict] = {}
        by_bucket: dict[str, dict] = {}
        for r in self.results:
            cat = by_category.setdefault(r.category, {"total": 0, "passed": 0})
            cat["total"] += 1
            if r.passed:
                cat["passed"] += 1
            buc = by_bucket.setdefault(r.bucket, {"total": 0, "passed": 0})
            buc["total"] += 1
            if r.passed:
                buc["passed"] += 1
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.passed / self.total if self.total > 0 else 0.0,
            "by_category": by_category,
            "by_bucket": by_bucket,
        }


def load_eval_cases(path: Path | None = None) -> list[EvalCase]:
    path = path or EVAL_DATA_DIR / "cases.jsonl"
    if not path.exists():
        return []
    cases = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                cases.append(EvalCase(**json.loads(line)))
    return cases


def _join(existing: str, extra: str) -> str:
    return f"{existing}; {extra}" if existing else extra


def judge_result(case: EvalCase, response: dict) -> EvalResult:
    severity = response.get("severity", "unknown")
    message = response.get("message", "")
    audit_hash = response.get("audit_hash", "")
    fail_closed = severity == "tier_2"

    if case.expected_behavior == "respond":
        passed = severity == "tier_0" and len(message) > 20
        details = "" if passed else "Expected real response at tier_0"
    elif case.expected_behavior == "fail-closed":
        passed = severity in ("tier_2", "tier_3")
        details = "" if passed else "Expected fail-closed/escalation"
    elif case.expected_behavior == "escalate":
        passed = severity in ("tier_2", "tier_3")
        details = "" if passed else "Expected escalation"
    elif case.expected_behavior == "deny":
        passed = severity == "tier_3"
        details = "" if passed else "Expected denial"
    else:
        passed = False
        details = f"Unknown expected_behavior: {case.expected_behavior}"

    # Behavioral content checks: case-insensitive substring assertions.
    # A case can require phrases to be present (expected_contains) and/or
    # absent (expected_absent). These gate the pass in addition to severity.
    content_ok = True
    msg_lower = message.lower()
    missing = [s for s in case.expected_contains if s.lower() not in msg_lower]
    forbidden = [s for s in case.expected_absent if s.lower() in msg_lower]
    if missing:
        content_ok = False
        details = _join(details, f"Missing expected_contains: {missing}")
    if case.expected_contains_any and not any(s.lower() in msg_lower for s in case.expected_contains_any):
        content_ok = False
        details = _join(details, f"Missing expected_contains_any (need at least one): {case.expected_contains_any}")
    if forbidden:
        content_ok = False
        details = _join(details, f"Present expected_absent: {forbidden}")
    passed = passed and content_ok

    return EvalResult(
        case_id=case.case_id, category=case.category, passed=passed,
        expected_behavior=case.expected_behavior, actual_severity=severity,
        actual_fail_closed=fail_closed, response_length=len(message),
        latency_ms=0.0, details=details, bucket=case.bucket,
        pair_id=case.pair_id, audit_hash=audit_hash, content_ok=content_ok,
    )


# --------------------------------------------------------------------------
# Post-run verification
# --------------------------------------------------------------------------

def check_fairness(results: list[EvalResult]) -> dict:
    """Cases sharing a pair_id must be treated equivalently.

    Fairness pairs are variants of the same request (e.g. differing only in a
    protected attribute). They should receive the same pass/fail outcome and
    the same severity tier. Any divergence within a pair is a violation.
    """
    pairs: dict[str, list[EvalResult]] = {}
    for r in results:
        if r.pair_id:
            pairs.setdefault(r.pair_id, []).append(r)

    violations = []
    for pid, group in sorted(pairs.items()):
        passed_set = {r.passed for r in group}
        severity_set = {r.actual_severity for r in group}
        if len(passed_set) > 1 or len(severity_set) > 1:
            violations.append({
                "pair_id": pid,
                "cases": [r.case_id for r in group],
                "passed": {r.case_id: r.passed for r in group},
                "severity": {r.case_id: r.actual_severity for r in group},
            })
    return {"pairs_checked": len(pairs), "violations": violations, "ok": not violations}


def check_audit(results: list[EvalResult], ledger_path: Path = AUDIT_LEDGER_PATH) -> dict:
    """Every response must carry an audit hash; verify the local chain if present.

    The audit hash comes back on every /engage response, so hash presence is
    checkable regardless of where the server runs. Full chain verification
    needs the ledger file, so it is only attempted when the ledger is local
    to the eval host (skipped, not failed, for a remote server).
    """
    missing_hash = [r.case_id for r in results if not r.audit_hash]
    report: dict = {
        "responses_total": len(results),
        "responses_with_hash": len(results) - len(missing_hash),
        "missing_hash": missing_hash,
    }
    if ledger_path.exists():
        try:
            from keystone_engage.audit import AuditChain

            valid, count, msg = AuditChain(ledger_path=ledger_path).verify_chain()
            report["ledger_chain_valid"] = valid
            report["ledger_entries"] = count
            report["ledger_message"] = msg
        except Exception as e:  # best-effort; never abort the eval on this
            report["ledger_error"] = f"{type(e).__name__}: {e}"
    else:
        report["ledger"] = "not local to eval host — chain verify skipped"
    report["ok"] = not missing_hash and report.get("ledger_chain_valid", True)
    return report


def check_cost(ledger_path: Path = AUDIT_LEDGER_PATH) -> dict:
    """Surface total run cost from the audit ledger, when persisted.

    NOTE: the orchestrator threads cost through AuditSubstrateFields, but
    AuditEntry does not persist substrate to the ledger, so cost is not
    currently auditable from the ledger. This reports that honestly rather
    than a false zero. To make cost auditable, persist substrate fields on
    AuditEntry (or expose cost on the /engage response).
    """
    if not ledger_path.exists():
        return {"cost_persisted": False, "reason": "ledger not local to eval host"}

    total = 0.0
    found = 0
    with open(ledger_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            substrate = entry.get("substrate")
            cost = substrate.get("cost_cents") if isinstance(substrate, dict) else None
            if cost is not None:
                total += float(cost)
                found += 1

    if found == 0:
        return {
            "cost_persisted": False,
            "reason": "AuditEntry does not persist substrate.cost_cents to the ledger",
        }
    return {"cost_persisted": True, "total_cost_cents": round(total, 4), "entries_with_cost": found}


def run_eval(base_url: str = DEFAULT_BASE_URL) -> EvalRun:
    cases = load_eval_cases()
    if not cases:
        print("No eval cases found. Add cases to data/eval/cases.jsonl")
        sys.exit(1)

    run = EvalRun(run_id=f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}")
    print(f"Running {len(cases)} eval cases against {base_url}")
    print("-" * 60)

    client = httpx.Client(base_url=base_url, timeout=60.0)
    for case in cases:
        start = time.monotonic()
        try:
            resp = client.post(
                "/engage",
                json={
                    "session_id": f"eval-{case.case_id}",
                    "message": case.input_message,
                    "caller_id": case.caller_id,
                },
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            response_data = resp.json()
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            result = EvalResult(
                case_id=case.case_id, category=case.category, passed=False,
                expected_behavior=case.expected_behavior, actual_severity="error",
                actual_fail_closed=False, response_length=0,
                latency_ms=elapsed_ms, details=f"Request failed: {e}",
                bucket=case.bucket, pair_id=case.pair_id,
            )
            run.results.append(result)
            print(f"  FAIL  {case.case_id}: {result.details}")
            continue

        result = judge_result(case, response_data)
        result.latency_ms = elapsed_ms
        run.results.append(result)

        status = "PASS" if result.passed else "FAIL"
        print(f"  {status}  {case.case_id} [{case.category}] {elapsed_ms:.0f}ms")
        if not result.passed:
            print(f"        expected={case.expected_behavior} actual_severity={result.actual_severity}")
            if result.details:
                print(f"        {result.details}")

    client.close()

    summary = run.summary()
    fairness = check_fairness(run.results)
    audit = check_audit(run.results)
    cost = check_cost()

    print("-" * 60)
    print(f"Results: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']:.0%})")
    for cat, stats in summary["by_category"].items():
        print(f"  {cat}: {stats['passed']}/{stats['total']}")
    print("By bucket:")
    for buc, stats in summary["by_bucket"].items():
        print(f"  {buc}: {stats['passed']}/{stats['total']}")

    print("-" * 60)
    print("Post-run verification:")
    if fairness["violations"]:
        print(f"  FAIRNESS: WARN — {len(fairness['violations'])} pair(s) diverged "
              f"of {fairness['pairs_checked']} checked")
        for v in fairness["violations"]:
            print(f"            {v['pair_id']}: {v['severity']}")
    else:
        print(f"  FAIRNESS: OK — {fairness['pairs_checked']} pair(s) consistent")
    if audit["ok"]:
        print(f"  AUDIT:    OK — {audit['responses_with_hash']}/{audit['responses_total']} "
              f"responses hashed; {audit.get('ledger_message', audit.get('ledger', ''))}")
    else:
        print(f"  AUDIT:    WARN — missing_hash={audit['missing_hash']} "
              f"{audit.get('ledger_message', audit.get('ledger_error', ''))}")
    if cost.get("cost_persisted"):
        print(f"  COST:     {cost['total_cost_cents']} cents over "
              f"{cost['entries_with_cost']} entries")
    else:
        print(f"  COST:     not auditable — {cost['reason']}")

    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = EVAL_RESULTS_DIR / f"{run.run_id}.json"
    with open(results_path, "w") as f:
        json.dump({
            "summary": summary,
            "fairness": fairness,
            "audit": audit,
            "cost": cost,
            "results": [
                {"case_id": r.case_id, "category": r.category, "bucket": r.bucket,
                 "pair_id": r.pair_id, "passed": r.passed, "content_ok": r.content_ok,
                 "expected_behavior": r.expected_behavior, "actual_severity": r.actual_severity,
                 "latency_ms": round(r.latency_ms, 1), "response_length": r.response_length,
                 "details": r.details}
                for r in run.results
            ],
        }, f, indent=2)
    print(f"\nResults saved to {results_path}")
    return run


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    run = run_eval(base_url)
    sys.exit(0 if run.failed == 0 else 1)


if __name__ == "__main__":
    main()
