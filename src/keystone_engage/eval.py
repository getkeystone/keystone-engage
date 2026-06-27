"""Eval harness for Keystone Engage.

Runs eval cases against a live server. Results saved to data/eval/results/.
Run: uv run python -m keystone_engage.eval
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
DEFAULT_BASE_URL = "http://localhost:8100"


@dataclass
class EvalCase:
    case_id: str
    category: str
    input_message: str
    expected_behavior: str
    description: str = ""
    caller_id: str | None = None


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
        for r in self.results:
            cat = by_category.setdefault(r.category, {"total": 0, "passed": 0})
            cat["total"] += 1
            if r.passed:
                cat["passed"] += 1
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.passed / self.total if self.total > 0 else 0.0,
            "by_category": by_category,
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


def judge_result(case: EvalCase, response: dict) -> EvalResult:
    severity = response.get("severity", "unknown")
    message = response.get("message", "")
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

    return EvalResult(
        case_id=case.case_id, category=case.category, passed=passed,
        expected_behavior=case.expected_behavior, actual_severity=severity,
        actual_fail_closed=fail_closed, response_length=len(message),
        latency_ms=0.0, details=details,
    )


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

    client.close()

    summary = run.summary()
    print("-" * 60)
    print(f"Results: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']:.0%})")
    for cat, stats in summary["by_category"].items():
        print(f"  {cat}: {stats['passed']}/{stats['total']}")

    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = EVAL_RESULTS_DIR / f"{run.run_id}.json"
    with open(results_path, "w") as f:
        json.dump({"summary": summary, "results": [
            {"case_id": r.case_id, "category": r.category, "passed": r.passed,
             "expected_behavior": r.expected_behavior, "actual_severity": r.actual_severity,
             "latency_ms": round(r.latency_ms, 1), "response_length": r.response_length,
             "details": r.details}
            for r in run.results
        ]}, f, indent=2)
    print(f"\nResults saved to {results_path}")
    return run


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    run = run_eval(base_url)
    sys.exit(0 if run.failed == 0 else 1)


if __name__ == "__main__":
    main()
