"""Integration tests for the EventBus.

These tests require a live NATS server (set via NATS_URL env; defaults to localhost:4222).
Skip with: pytest -m "not integration"
"""

import asyncio
import uuid

import pytest
import pytest_asyncio

from keystone_engage.eventbus import EventBus, TaskEvent, SUBJECT_PREFIX


pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def bus():
    b = EventBus()
    await b.connect()
    yield b
    await b.close()


class TestEventBusConnectivity:
    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        bus = EventBus()
        await bus.connect()
        assert bus.connected
        await bus.close()
        assert not bus.connected

    @pytest.mark.asyncio
    async def test_stream_exists_after_connect(self, bus):
        info = await bus._js.find_stream_name_by_subject(f"{SUBJECT_PREFIX}.>")
        assert info == "KEYSTONE_TASKS"


class TestPublishSubscribe:
    @pytest.mark.asyncio
    async def test_publish_and_receive(self, bus):
        received: list[TaskEvent] = []

        async def handler(event: TaskEvent):
            received.append(event)

        await bus.subscribe(f"{SUBJECT_PREFIX}.created", handler)

        task_id = str(uuid.uuid4())
        await bus.publish_task_event(task_id, "created", "test-agent", {"test": True})

        # Give the message time to arrive
        await asyncio.sleep(0.5)

        matching = [e for e in received if e.task_id == task_id]
        assert len(matching) == 1
        assert matching[0].task_id == task_id
        assert received[0].event_type == "created"
        assert received[0].agent_id == "test-agent"
        assert received[0].payload == {"test": True}

    @pytest.mark.asyncio
    async def test_publish_multiple_event_types(self, bus):
        received: list[TaskEvent] = []

        async def handler(event: TaskEvent):
            received.append(event)

        await bus.subscribe_all(handler)

        task_id = str(uuid.uuid4())
        await bus.publish_task_event(task_id, "created", "agent-1")
        await bus.publish_task_event(task_id, "claimed_by_agent", "agent-1")
        await bus.publish_task_event(task_id, "completed", "agent-1")

        await asyncio.sleep(0.5)

        matching = [e for e in received if e.task_id == task_id]
        assert len(matching) >= 3
        event_types = [e.event_type for e in matching]
        assert "created" in event_types
        assert "claimed_by_agent" in event_types
        assert "completed" in event_types


class TestTaskEventSerialization:
    def test_roundtrip(self):
        event = TaskEvent(
            task_id="abc-123",
            event_type="stuck",
            agent_id="agent-1",
            payload={"reason": "heartbeat timeout"},
        )
        data = event.to_json()
        restored = TaskEvent.from_json(data)
        assert restored.task_id == "abc-123"
        assert restored.event_type == "stuck"
        assert restored.agent_id == "agent-1"
        assert restored.payload == {"reason": "heartbeat timeout"}

    def test_repr(self):
        event = TaskEvent(task_id="abcdefgh-1234", event_type="created", agent_id="agent-1")
        assert "created" in repr(event)
        assert "abcdefgh" in repr(event)
