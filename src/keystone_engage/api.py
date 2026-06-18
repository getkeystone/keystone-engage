"""FastAPI application for Keystone Engage.

API layer for the governed conversational agent. Instrumented with
OpenTelemetry GenAI semantic conventions from day one.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from keystone_engage import __version__
from keystone_engage.models import EngageRequest, EngageResponse, HealthResponse
from keystone_engage.observability import setup_telemetry
from keystone_engage.orchestrator import EngageOrchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Keystone Engage",
    description=(
        "Governed conversational agent for regulated customer interaction. "
        "Part of the Keystone Applied Intelligence platform."
    ),
    version=__version__,
)

# Setup OTel instrumentation on the FastAPI app
_tracer = setup_telemetry(app)

# Orchestrator instance
_orchestrator = EngageOrchestrator()


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@app.post("/engage", response_model=EngageResponse)
async def engage(request: EngageRequest) -> EngageResponse:
    return await _orchestrator.handle(request)
