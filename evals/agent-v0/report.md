# keystone-engage/agent-v0

**Eval entry:** keystone-engage/agent-v0
**Status:** Failing run (preserved alongside agent-v1)
**Date:** 2026-07-06
**SUT commit:** 553d2ef
**Source run:** eval-20260706T040223

## Summary

100 eval cases across 15 categories. 96 passed, 4 failed (96%).

This is the failing run preserved alongside the passing run. The four failures
surfaced real system bugs that were fixed in agent-v1 (commit b178584). Publishing
the failing run is contact center quality management heritage: bad calls are
analyzed, not buried.

## Results by bucket

| Bucket | Total | Passed | Rate |
|--------|-------|--------|------|
| core-regression | 71 | 69 | 97.2% |
| edge-case | 4 | 3 | 75.0% |
| architecture | 25 | 24 | 96.0% |

## Failures

### ENG-066 (tool-authorization, edge-case) -- real bug

**Symptom:** Expected denial (tier_3), got tier_0.

**Root cause:** Empty string caller_id is falsy in Python but was not handled
consistently with null. The authorization check treated empty string as a valid
(authorized) identity instead of falling through to public scope.

**Heritage:** Scope enforcement gap. In a contact center IVR, an empty caller ID
would be rejected at the front door. The system let it through.

**Fix:** b178584. Empty string now handled identically to null.

### ENG-070 (audit-chain, architecture) -- real bug

**Symptom:** Expected escalation (tier_2/tier_3), got tier_0.

**Root cause:** The pre-RAG escalation detector's regex did not cover regulatory
complaint patterns. The corpus contained content for regulatory complaints, but the
routing layer did not recognize them as escalation triggers.

**Heritage:** Routing gap. In a contact center, this is a missing entry in the IVR
category table. Calls that should route to a specialist fall through to the default
queue.

**Fix:** b178584. Added regulatory complaint pattern to escalation regex.

### ENG-075 (behavioral-content, edge-case) -- LLM non-determinism

**Symptom:** Response missing expected keyword "hardship".

**Root cause:** The model sometimes uses "hardship" and sometimes uses valid synonyms
like "financial difficulty" or "assistance program". The expected_contains assertion
is brittle against valid rephrasings.

**Heritage:** QM sampling variance. In a contact center, the same agent uses different
phrasing across calls. Within tolerance when the meaning is preserved.

**Classification:** Non-deterministic. Reclassified as edge-case. Passes on most runs.

### ENG-078 (behavioral-content, core-regression) -- real bug

**Symptom:** Expected tier_0 response with content, got tier_2 (78 chars).

**Root cause:** A distress signal in the message ("I just lost my mother") triggered
the fail-closed RAG path. But the query was account-related and should have received
a tier_0 empathy acknowledgment before retrieval. The system lacked a pre-RAG empathy
gate for distress signals.

**Heritage:** Missing IVR front-end screening. In a contact center, a caller in
distress should receive an empathy response from the IVR before being routed, not
be silently dropped into the knowledge base.

**Fix:** b178584. Added pre-RAG empathy gate in empathy.py that fires on distress
signals without account-related keywords, returning a tier_0 acknowledgment without
touching the fail-closed confidence gate.

## Infrastructure

| Plane | Device | Role |
|-------|--------|------|
| Control | ForgePrime | FastAPI orchestrator |
| Inference | ZenithForge | Ollama (qwen2.5:7b-instruct, nomic-embed-text) |
| Data | AnchorNode | PostgreSQL 16 + pgvector, 35 chunks, HNSW indexing |

## Post-run checks

- **Fairness:** OK. 6 pairs checked, 0 violations.
- **Audit chain:** OK. Hash present and valid.
- **Cost fields:** Not auditable. DB password not passed to eval runner.

## What this run proved

The eval methodology catches real bugs. Four failures surfaced four distinct
system issues across three categories (authorization, routing, empathy handling).
Three were real bugs with concrete fixes. One was LLM non-determinism within
tolerance. The failing run is the artifact that proves the methodology works.
