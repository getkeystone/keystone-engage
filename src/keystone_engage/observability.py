"""OpenTelemetry GenAI semantic conventions for Keystone Engage.

Emits spans following the OTel GenAI conventions from day one (graduation path 1.4).
Span types: invoke_agent, execute_tool, gen_ai.* attributes for model calls.
"""

from __future__ import annotations

import os
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
    else:
        exporter = ConsoleSpanExporter()

    provider.add_span_processor(BatchSpanProcessor(exporter))
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
