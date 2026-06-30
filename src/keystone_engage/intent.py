"""Pre-RAG intent classification for Keystone Engage.

Detects creative, entertainment, and general-knowledge requests that
should not reach the RAG pipeline. Same pattern as escalation detection:
check before RAG, route to fail-closed without wasting an LLM call.

This is the structural fix for the semantic overlap finding (ENG-012,
ENG-036) where corpus vocabulary in adversarial creative requests
defeats the confidence threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class IntentResult:
    is_off_topic: bool
    reason: str = ""


_CREATIVE_PATTERNS = re.compile(
    r"\b(write\s+(me\s+)?(a\s+)?(poem|story|song|essay|letter|haiku|limerick|joke|script|code|email|resume|cv)|"
    r"compose\s+(a\s+)?(poem|song|melody|story)|"
    r"create\s+(a\s+)?(poem|story|song|fictional|creative)|"
    r"tell\s+(me\s+)?(a\s+)?(joke|story|riddle)|"
    r"make\s+(me\s+)?(a\s+)?(poem|joke|story|meme)|"
    r"sing\s+(me\s+)?|"
    r"can\s+you\s+(write|compose|create|sing|draw|paint))\b",
    re.IGNORECASE,
)

_GENERAL_KNOWLEDGE_PATTERNS = re.compile(
    r"\b(what\s+(is|are)\s+the\s+(weather|temperature|time|date|capital|population|president|prime\s+minister)|"
    r"who\s+(is|was|are|were)\s+(the\s+)?(president|prime|king|queen|ceo|founder)|"
    r"how\s+(tall|old|far|long|big)\s+is\s+|"
    r"what\s+year\s+(was|did|is)|"
    r"where\s+is\s+(the\s+)?(nearest|closest|best)|"
    r"translate\s+|"
    r"what\s+(is|are)\s+the\s+(news|score|results)|"
    r"help\s+me\s+(with\s+my\s+)?(homework|assignment|thesis|resume|cv|cover\s+letter))\b",
    re.IGNORECASE,
)

_ENTERTAINMENT_PATTERNS = re.compile(
    r"\b(play\s+(a\s+)?(game|trivia|quiz)|"
    r"let.?s\s+play|"
    r"recommend\s+(me\s+)?(a\s+)?(movie|book|show|restaurant|game|song)|"
    r"what\s+should\s+I\s+(watch|read|listen|play|eat|cook)|"
    r"fun\s+fact|"
    r"would\s+you\s+rather|"
    r"truth\s+or\s+dare)\b",
    re.IGNORECASE,
)


def check_intent(message: str) -> IntentResult:
    """Check if the message is off-topic before RAG processing.

    Returns immediately on first match. Creative requests take priority
    because they are the most common adversarial probe pattern.
    """
    if _CREATIVE_PATTERNS.search(message):
        return IntentResult(
            is_off_topic=True,
            reason="Creative or generative request detected. Outside engagement scope.",
        )

    if _GENERAL_KNOWLEDGE_PATTERNS.search(message):
        return IntentResult(
            is_off_topic=True,
            reason="General knowledge query detected. Outside engagement scope.",
        )

    if _ENTERTAINMENT_PATTERNS.search(message):
        return IntentResult(
            is_off_topic=True,
            reason="Entertainment request detected. Outside engagement scope.",
        )

    return IntentResult(is_off_topic=False)
