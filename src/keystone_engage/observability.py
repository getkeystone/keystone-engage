"""OpenTelemetry GenAI semantic conventions for Keystone Engage.

Emits spans following the OTel GenAI conventions from day one (graduation path 1.4).
Span types: invoke_agent, execute_tool, gen_ai.* attributes for model calls.

Substrate attributes (day-one substrate package):
  keystone.agent_id, keystone.agent_tempo, keystone.task_id,
  keystone.priority, keystone.cost_cents, keystone.budget_remaining_cents
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Any, Generator

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

_TRACER_NAME = "keystone-engage"


def setup_telemetry(app: Any | None = None) -> trace.Tracer:
    """Initialize OTel with GenAI semantic conventions.

    Exports to OTLP endpoint (SolsticeNode) if configured,
    falls back to console for local development.
    ConsoleSpanExporter is skipped under pytest to avoid
    teardown-ordering noise (ValueError on closed stdout).
    """
    resource = Resource.create(
        {
            "service.name": "keystone-engage",
            "service.version": "0.1.0",
            "deployment.environment": os.getenv("KEYSTONE_ENV", "development"),
        }
    )

    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    elif "pytest" not in sys.modules:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    if app is not None:
        FastAPIInstrumentor.instrument_app(app)

    return trace.get_tracer(_TRACER_NAME)


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


@contextmanager
def agent_span(
    tracer: trace.Tracer,
    agent_name: str,
    session_id: str,
    **attributes: Any,
) -> Generator[trace.Span, None, None]:
    """Create an invoke_agent span with GenAI attributes."""
    with tracer.start_as_current_span(
        "invoke_agent",
        attributes={
            "gen_ai.system": "keystone-engage",
            "gen_ai.agent.name": agent_name,
            "keystone.session_id": session_id,
            **attributes,
        },
    ) as span:
        yield span


@contextmanager
def tool_span(
    tracer: trace.Tracer,
    tool_name: str,
    **attributes: Any,
) -> Generator[trace.Span, None, None]:
    """Create an execute_tool span with GenAI attributes."""
    with tracer.start_as_current_span(
        "execute_tool",
        attributes={
            "gen_ai.tool.name": tool_name,
            **attributes,
        },
    ) as span:
        yield span


@contextmanager
def llm_span(
    tracer: trace.Tracer,
    model: str,
    **attributes: Any,
) -> Generator[trace.Span, None, None]:
    """Create a model call span with GenAI token tracking attributes."""
    with tracer.start_as_current_span(
        "gen_ai.chat",
        attributes={
            "gen_ai.system": "ollama",
            "gen_ai.request.model": model,
            **attributes,
        },
    ) as span:
        yield span


def record_token_usage(span: trace.Span, input_tokens: int, output_tokens: int) -> None:
    """Record token usage on a span per GenAI conventions."""
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)


def record_substrate_attributes(
    span: trace.Span,
    agent_id: str | None = None,
    agent_tempo: str | None = None,
    task_id: str | None = None,
    priority: int | None = None,
    cost_cents: float | None = None,
    budget_remaining_cents: float | None = None,
) -> None:
    """Record substrate dimensions on a span.

    Called once after task creation (agent_id, tempo, task_id, priority,
    budget) and again after the RAG response (cost_cents, budget update).
    Only sets attributes that are not None, so callers pass what they have.

    Contact center heritage: these are the per-interaction metadata fields
    that every contact center compliance system recorded on every call.
    """
    if agent_id is not None:
        span.set_attribute("keystone.agent_id", agent_id)
    if agent_tempo is not None:
        span.set_attribute("keystone.agent_tempo", agent_tempo)
    if task_id is not None:
        span.set_attribute("keystone.task_id", task_id)
    if priority is not None:
        span.set_attribute("keystone.priority", priority)
    if cost_cents is not None:
        span.set_attribute("keystone.cost_cents", cost_cents)
    if budget_remaining_cents is not None:
        span.set_attribute("keystone.budget_remaining_cents", budget_remaining_cents)
