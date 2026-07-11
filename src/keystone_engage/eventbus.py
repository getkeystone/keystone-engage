"""Event bus for Keystone Engage via NATS JetStream.

Publishes task lifecycle events to NATS on TrustNode. Agents and
monitors subscribe to events they care about. The event bus is the
backbone for tempo-heterogeneous dispatch: fast agents publish,
slow monitors subscribe, and the bus bridges the tempo gap.

Subject hierarchy:
  keystone.tasks.created          - task entered the queue
  keystone.tasks.claimed          - agent accepted the task
  keystone.tasks.heartbeat        - agent is alive and working
  keystone.tasks.completed        - agent finished
  keystone.tasks.failed           - retriable failure
  keystone.tasks.stuck            - no heartbeat received
  keystone.tasks.rescheduled      - task reassigned to new agent
  keystone.tasks.verified         - supervisor approved outcome
  keystone.tasks.unrecoverable    - dead letter

Stream: KEYSTONE_TASKS (JetStream, durable, replays from any point)

Contact center heritage: this is the CTI event stream. Every call
state change is published. Wallboards, WFM, QM, and reporting all
subscribe to the same stream. The event bus is the integration
backbone, not point-to-point RPC.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine
from uuid import UUID

import nats
from nats.js.api import StreamConfig, RetentionPolicy

logger = logging.getLogger(__name__)

DEFAULT_NATS_URL = "nats://100.71.252.101:4222"
STREAM_NAME = "KEYSTONE_TASKS"
SUBJECT_PREFIX = "keystone.tasks"

# Map TaskState values to event subject suffixes
_STATE_TO_SUBJECT = {
    "created": "created",
    "claimed_by_agent": "claimed",
    "in_progress": "heartbeat",  # in_progress transitions emit heartbeat-start
    "stuck": "stuck",
    "rescheduled": "rescheduled",
    "completed": "completed",
    "completed_verified": "verified",
    "failed": "failed",
    "failed_unrecoverable": "unrecoverable",
}


class TaskEvent:
    """A task lifecycle event published to the bus."""

    def __init__(
        self,
        task_id: str,
        event_type: str,
        agent_id: str,
        payload: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ):
        self.task_id = task_id
        self.event_type = event_type
        self.agent_id = agent_id
        self.payload = payload or {}
        self.timestamp = timestamp or datetime.now(timezone.utc)

    def to_json(self) -> bytes:
        return json.dumps({
            "task_id": self.task_id,
            "event_type": self.event_type,
            "agent_id": self.agent_id,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }).encode()

    @classmethod
    def from_json(cls, data: bytes) -> TaskEvent:
        d = json.loads(data)
        return cls(
            task_id=d["task_id"],
            event_type=d["event_type"],
            agent_id=d["agent_id"],
            payload=d.get("payload", {}),
            timestamp=datetime.fromisoformat(d["timestamp"]),
        )

    def __repr__(self) -> str:
        return f"TaskEvent({self.event_type}, task={self.task_id[:8]}, agent={self.agent_id})"


# Type alias for event handler callbacks
EventHandler = Callable[[TaskEvent], Coroutine[Any, Any, None]]


class EventBus:
    """NATS JetStream event bus for task lifecycle events.

    Usage:
        bus = EventBus()
        await bus.connect()
        await bus.publish_task_event(task_id, "created", agent_id, payload)
        await bus.subscribe("keystone.tasks.stuck", handler)
        await bus.close()
    """

    def __init__(self, nats_url: str = DEFAULT_NATS_URL) -> None:
        self._nats_url = nats_url
        self._nc = None  # nats.aio.client.Client
        self._js = None
        self._subscriptions: list = []

    @property
    def connected(self) -> bool:
        return self._nc is not None and not self._nc.is_closed

    async def connect(self) -> None:
        """Connect to NATS and ensure the JetStream stream exists."""
        self._nc = await nats.connect(self._nats_url)
        self._js = self._nc.jetstream()

        # Ensure the stream exists (idempotent)
        try:
            await self._js.find_stream_name_by_subject(f"{SUBJECT_PREFIX}.>")
            logger.info("EventBus: stream %s exists", STREAM_NAME)
        except nats.js.errors.NotFoundError:
            await self._js.add_stream(
                config=StreamConfig(
                    name=STREAM_NAME,
                    subjects=[f"{SUBJECT_PREFIX}.>"],
                    retention=RetentionPolicy.LIMITS,
                    max_msgs=100_000,
                    max_bytes=256 * 1024 * 1024,  # 256MB
                ),
            )
            logger.info("EventBus: created stream %s", STREAM_NAME)

        logger.info("EventBus: connected to %s", self._nats_url)

    async def close(self) -> None:
        """Drain subscriptions and disconnect."""
        if self._nc and self._nc.is_connected:
            for sub in self._subscriptions:
                try:
                    await sub.unsubscribe()
                except Exception:
                    pass
            try:
                await self._nc.drain()
            except Exception:
                await self._nc.close()
            self._nc = None
            self._js = None
            self._subscriptions.clear()
            logger.info("EventBus: disconnected")

    def _subject_for_state(self, state: str) -> str:
        """Map a task state to a NATS subject."""
        suffix = _STATE_TO_SUBJECT.get(state, state)
        return f"{SUBJECT_PREFIX}.{suffix}"

    async def publish_task_event(
        self,
        task_id: str | UUID,
        state: str,
        agent_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Publish a task lifecycle event to JetStream."""
        if not self._js:
            logger.warning("EventBus: not connected, skipping publish")
            return

        event = TaskEvent(
            task_id=str(task_id),
            event_type=state,
            agent_id=agent_id,
            payload=payload,
        )
        subject = self._subject_for_state(state)
        ack = await self._js.publish(subject, event.to_json())
        logger.debug(
            "EventBus: published %s to %s (seq=%d)",
            event.event_type, subject, ack.seq,
        )

    async def subscribe(
        self,
        subject: str,
        handler: EventHandler,
        durable: str | None = None,
    ) -> None:
        """Subscribe to task lifecycle events.

        The handler receives a TaskEvent for each message.
        If durable is set, the subscription survives reconnects.
        """
        if not self._js:
            raise RuntimeError("EventBus: not connected")

        async def _msg_handler(msg):
            try:
                event = TaskEvent.from_json(msg.data)
                await handler(event)
                await msg.ack()
            except Exception as e:
                logger.error("EventBus: handler error on %s: %s", msg.subject, e)

        if durable:
            sub = await self._js.subscribe(subject, cb=_msg_handler, durable=durable)
        else:
            sub = await self._js.subscribe(subject, cb=_msg_handler)

        self._subscriptions.append(sub)
        logger.info("EventBus: subscribed to %s (durable=%s)", subject, durable)

    async def subscribe_all(
        self,
        handler: EventHandler,
        durable: str | None = None,
    ) -> None:
        """Subscribe to all task lifecycle events."""
        await self.subscribe(f"{SUBJECT_PREFIX}.>", handler, durable=durable)
