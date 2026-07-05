"""Task lifecycle store for Keystone Engage.

Persists tasks to AnchorNode (PostgreSQL). Each orchestrator dispatch
creates a task row. State transitions are recorded. The audit chain
references task_id for provenance.

Contact center heritage: task state is the disposition machine.
Created -> in_progress -> completed|failed mirrors the contact
center interaction lifecycle.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Generator

import psycopg2

from keystone_engage.substrate.models import TaskState

logger = logging.getLogger(__name__)


class TaskStore:
    """Task lifecycle backed by PostgreSQL on AnchorNode."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        logger.info("TaskStore: connected to AnchorNode")

    @contextmanager
    def _conn(self) -> Generator:
        conn = psycopg2.connect(self._database_url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_task(
        self,
        owner_agent_id: str,
        payload: dict[str, Any],
        budget_cents: int,
    ) -> uuid.UUID:
        """Create a task row. Returns the generated task_id."""
        task_id = uuid.uuid4()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO tasks
                       (task_id, owner_agent_id, state, payload, budget_cents)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (
                        str(task_id),
                        owner_agent_id,
                        TaskState.CREATED.value,
                        json.dumps(payload),
                        budget_cents,
                    ),
                )
        logger.debug("Task created: %s for %s", task_id, owner_agent_id)
        return task_id

    def update_state(self, task_id: uuid.UUID, state: TaskState) -> None:
        """Transition a task to a new state."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET state = %s WHERE task_id = %s",
                    (state.value, str(task_id)),
                )
        logger.debug("Task %s -> %s", task_id, state.value)
