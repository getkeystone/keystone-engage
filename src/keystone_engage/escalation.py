"""Pre-RAG escalation detection for Keystone Engage.

Checks inbound messages for patterns that require immediate routing to
HITL without generating an automated response. Maps to contact center
bot-to-human escalation: certain utterances bypass the bot entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class EscalationTrigger(str, Enum):
    SUPERVISOR_REQUEST = "supervisor_request"
    CRISIS_SIGNAL = "crisis_signal"
    LEGAL_MENTION = "legal_mention"
    DISCRIMINATION = "discrimination"
    CEASE_CONTACT = "cease_contact"


@dataclass
class EscalationResult:
    should_escalate: bool
    trigger: EscalationTrigger | None = None
    reason: str = ""


# Patterns checked before RAG. Order matters: crisis first.
_PATTERNS: list[tuple[EscalationTrigger, re.Pattern, str]] = [
    (
        EscalationTrigger.CRISIS_SIGNAL,
        re.compile(
            r"\b(self[- ]?harm|suicid|kill\s+(my|him|her|them)self|end\s+(my|it\s+all)|"
            r"don.?t\s+want\s+to\s+(live|be\s+here)|no\s+reason\s+to\s+live|hurt(ing)?\s+(my|him|her|them)self)\b",
            re.IGNORECASE,
        ),
        "Crisis signal detected. Immediate human escalation required.",
    ),
    (
        EscalationTrigger.SUPERVISOR_REQUEST,
        re.compile(
            r"\b(speak\s+to\s+a?\s*(supervisor|manager|human|person|someone\s+else)|"
            r"transfer\s+me|escalate|talk\s+to\s+(a\s+)?(real|actual)\s+(person|human)|"
            r"let\s+me\s+speak\s+to|get\s+me\s+(a\s+)?(supervisor|manager))\b",
            re.IGNORECASE,
        ),
        "Customer requested supervisor or human agent.",
    ),
    (
        EscalationTrigger.LEGAL_MENTION,
        re.compile(
            r"\b(my\s+(lawyer|attorney|solicitor)|legal\s+(action|representation|counsel)|"
            r"i.?ll\s+sue|lawsuit|litigation|hear\s+from\s+my\s+(lawyer|attorney))\b",
            re.IGNORECASE,
        ),
        "Customer referenced legal representation or action.",
    ),
    (
        EscalationTrigger.DISCRIMINATION,
        re.compile(
            r"\b(discriminat\w*|racist|sexist|bias\w*|prejudic\w*|treat(ed|ing)\s+me\s+unfairly)",
            re.IGNORECASE,
        ),
        "Customer alleged discriminatory treatment.",
    ),
    (
        EscalationTrigger.LEGAL_MENTION,
        re.compile(
            r"\b(file\s+(a\s+)?complaint|report\s+(you|this)\s+to|"
            r"consumer\s+protection|regulatory\s+(complaint|authority|body)|"
            r"going\s+to\s+(report|complain)|better\s+business\s+bureau|"
            r"ombudsman)\b",
            re.IGNORECASE,
        ),
        "Customer referenced regulatory complaint or consumer protection.",
    ),
]


def check_escalation(message: str) -> EscalationResult:
    """Check message for escalation triggers before RAG processing.

    Returns immediately on first match. Crisis signals take priority.
    """
    for trigger, pattern, reason in _PATTERNS:
        if pattern.search(message):
            return EscalationResult(
                should_escalate=True,
                trigger=trigger,
                reason=reason,
            )

    return EscalationResult(should_escalate=False)
