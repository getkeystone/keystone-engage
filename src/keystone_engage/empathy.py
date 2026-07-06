"""Pre-RAG empathy detection for Keystone Engage.

Detects emotional distress messages that passed escalation (not crisis)
and intent classification (not off-topic) but contain no concrete
account-related question. These messages score low on cosine similarity
against factual corpus chunks and fail-close at the confidence gate.

The empathy gate returns a templated acknowledgment that invites the
customer to share their specific situation, routing them toward content
the RAG pipeline can serve.

Contact center heritage: empathy acknowledgment before routing is
standard bot design. The agent does not attempt to solve before
understanding the customer's specific need.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class EmpathyResult:
    is_distress: bool
    reason: str = ""
    response: str = ""


# Distress signals: emotional language without a concrete question.
_DISTRESS_PATTERNS = re.compile(
    r"\b(so\s+stress(ful|ed|ing)|very\s+stress(ful|ed|ing)|"
    r"overwhelm(ed|ing)|can.?t\s+(cope|handle|deal\s+with|take\s+(it|this))|"
    r"cannot\s+(cope|handle|deal\s+with|take\s+(it|this))|"
    r"do\s+not\s+know\s+what\s+to\s+do|don.?t\s+know\s+what\s+to\s+do|"
    r"feel(ing)?\s+(hopeless|helpless|lost|trapped|desperate|scared|terrified)|"
    r"at\s+(my|the)\s+(wit.?s\s+end|end\s+of\s+(my|the)\s+rope)|"
    r"falling\s+apart|breaking\s+down|"
    r"no\s+way\s+out|nowhere\s+to\s+turn)\b",
    re.IGNORECASE,
)

# Account-related keywords: if present alongside distress, let RAG handle it.
# The customer has a concrete question even if they are upset.
# Nouns are pluralized (s?) so "payments"/"bills"/"plans" anchor correctly;
# a bare "payment" would otherwise fail the trailing \b against "payments".
_ACCOUNT_KEYWORDS = re.compile(
    r"\b(payments?|balances?|accounts?|arrangements?|plans?|debts?|bills?|"
    r"owe|owed|owing|overdue|past\s+due|late\s+fee|interest|"
    r"hardship|defer|deferral|settle|settlement|"
    r"creditor|collector|collection|"
    r"job|income|employ|laid\s+off|fired|"
    r"surgery|hospital|medical|disability|"
    r"divorce|spouse|estate|flood|fire|"
    r"options?|programs?|rights?)\b",
    re.IGNORECASE,
)

_EMPATHY_RESPONSE = (
    "I understand this is a difficult and stressful situation. "
    "I want to help you find the right options. "
    "Could you tell me more about your specific circumstances "
    "so I can see what programs or arrangements might be available to you?"
)


def check_empathy(message: str) -> EmpathyResult:
    """Check for diffuse emotional distress before RAG processing.

    Fires only when distress signals are present AND no account-related
    keywords anchor the message to a concrete question. If the customer
    mentions both distress and a specific topic (payment, hardship, job
    loss), let RAG handle it since it can ground the response in corpus
    content.

    Returns immediately. Does not call the LLM.
    """
    if _DISTRESS_PATTERNS.search(message) and not _ACCOUNT_KEYWORDS.search(message):
        return EmpathyResult(
            is_distress=True,
            reason="Emotional distress without concrete account question. Empathy acknowledgment before routing.",
            response=_EMPATHY_RESPONSE,
        )
    return EmpathyResult(is_distress=False)
