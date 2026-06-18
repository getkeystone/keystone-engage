"""Eval harness for Keystone Engage.

Follows the keystone-core eval methodology: every failing run is preserved
alongside the passing run, the eval set grows from adversarial discovery,
and results are hash-chained to the audit ledger.

This harness will publish keystone-engage/agent-v1 as the baseline eval.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

EVAL_DATA_DIR = Path("data/eval")


@dataclass
class EvalCase:
    case_id: str
    category: str
    input_message: str
    expected_behavior: str  # "pass", "fail-closed", "escalate", "deny"
    description: str = ""


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    actual_behavior: str
    details: str = ""
    duration_ms: float = 0.0


@dataclass
class EvalRun:
    run_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cases: list[EvalResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.passed / self.total if self.total > 0 else 0.0,
        }


def load_eval_cases(path: Path | None = None) -> list[EvalCase]:
    """Load eval cases from JSONL file."""
    path = path or EVAL_DATA_DIR / "cases.jsonl"
    if not path.exists():
        logger.warning("No eval cases found at %s", path)
        return []

    cases = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                data = json.loads(line)
                cases.append(EvalCase(**data))
    return cases


def main() -> None:
    """Entry point for `make eval`."""
    cases = load_eval_cases()
    if not cases:
        print("No eval cases found. Add cases to data/eval/cases.jsonl")
        print("Format: one JSON object per line with fields:")
        print("  case_id, category, input_message, expected_behavior, description")
        return

    print(f"Loaded {len(cases)} eval cases")
    print("Eval execution not yet implemented. Scaffold only.")


if __name__ == "__main__":
    main()
