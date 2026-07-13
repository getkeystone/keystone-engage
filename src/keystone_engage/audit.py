"""Hash-chained audit log for Keystone Engage.

Compatible with keystone-core audit format. Append-only, tamper-evident.
Maps to contact center compliance logging: every routing decision, disposition
code, and disclosure event is recorded in a tamper-evident trail.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keystone_engage.models import AuditEntry
from keystone_engage.substrate.models import AuditSubstrateFields

logger = logging.getLogger(__name__)


class AuditChain:
    """Append-only hash-chained audit ledger.

    Writes JSONL to a local file. In production, writes to Data-Plane (PostgreSQL)
    and archives to the storage plane. The chain is the same format used by keystone-core.

    The substrate parameter is accepted for interface compatibility with PgAuditChain
    but is not written to dedicated columns (JSONL has no columns). The orchestrator
    already puts substrate-relevant data into the payload dict, so the JSONL record
    is complete without separate substrate fields.
    """

    def __init__(self, ledger_path: Path | str = "data/audit/ledger.jsonl") -> None:
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash = self._read_last_hash()

    def _read_last_hash(self) -> str:
        """Read the last hash from the ledger file, or return empty for genesis."""
        if not self.ledger_path.exists():
            return ""
        last_line = ""
        with open(self.ledger_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line
        if not last_line:
            return ""
        try:
            entry = json.loads(last_line)
            return entry.get("curr_hash", "")
        except json.JSONDecodeError:
            logger.warning("Corrupt last line in audit ledger, starting new chain")
            return ""

    def append(
        self,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        substrate: AuditSubstrateFields | None = None,
    ) -> AuditEntry:
        """Append a hash-chained entry to the audit ledger."""
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            actor=actor,
            payload=payload or {},
        )
        entry.compute_hash(self._last_hash)
        self._last_hash = entry.curr_hash

        with open(self.ledger_path, "a") as f:
            f.write(entry.model_dump_json() + "\n")

        logger.debug("Audit: %s by %s -> %s", event_type, actor, entry.curr_hash[:12])
        return entry

    def verify_chain(self) -> tuple[bool, int, str]:
        """Verify the entire chain. Returns (valid, entry_count, message)."""
        if not self.ledger_path.exists():
            return True, 0, "Empty ledger"

        prev_hash = ""
        count = 0

        with open(self.ledger_path) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    return False, count, f"Line {line_num}: invalid JSON"

                entry = AuditEntry(**data)
                stored_hash = entry.curr_hash

                # Recompute and compare
                entry.compute_hash(prev_hash)
                if entry.curr_hash != stored_hash:
                    return (
                        False,
                        count,
                        f"Line {line_num}: hash mismatch (expected {entry.curr_hash[:12]}, "
                        f"stored {stored_hash[:12]})",
                    )

                if entry.prev_hash != prev_hash:
                    return (
                        False,
                        count,
                        f"Line {line_num}: prev_hash mismatch",
                    )

                prev_hash = stored_hash
                count += 1

        return True, count, f"Chain intact: {count} entries verified"
