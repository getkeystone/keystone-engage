"""Task lifecycle store for Keystone Engage.

v2 adds validated transitions, heartbeat tracking, stuck detection,
and takeover protocol. Persists to AnchorNode (PostgreSQL).

Contact center heritage:
  create_task     = call enters queue
  claim_task      = agent accepts the call
  heartbeat       = agent presence signal (ACD wrap timer)
  mark_stuck      = agent went silent, call eligible for transfer
  takeover        = call transferred to another agent
  verify_task     = supervisor reviews the outcome
  update_state    = disposition code applied

The state machine rejects invalid transitions. A task in COMPLETED
cannot go to IN_PROGRESS. A task in FAILED_UNRECOVERABLE is terminal.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

import psycopg2

from keystone_engage.substrate.models import (
    InvalidTransition,
    TaskState,
    V2_DEFAULT_HEARTBEAT_INTERVAL_S,
    V2_STUCK_THRESHOLD_MULTIPLIER,
    validate_transition,
)

logger = logging.getLogger(__name__)


class TaskStore:
    """Task lifecycle backed by PostgreSQL on AnchorNode.

    v2 methods:
      claim_task()      : agent claims a created task
      heartbeat()       : agent signals it is alive
      get_stuck_tasks() : find tasks with stale heartbeats
      mark_stuck()      : transition stale tasks to stuck
      takeover()        : reassign a stuck/failed task to a new agent
      verify_task()     : mark a completed task as verified
    """

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

    def _get_current_state(self, cur, task_id: uuid.UUID) -> TaskState | None:
        """Read the current state of a task. Returns None if not found."""
        cur.execute("SELECT state FROM tasks WHERE task_id = %s", (str(task_id),))
        row = cur.fetchone()
        if row is None:
            return None
        return TaskState(row[0])

    # ---------------------------------------------------------------
    # Core lifecycle
    # ---------------------------------------------------------------

    def create_task(
        self,
        owner_agent_id: str,
        payload: dict[str, Any],
        budget_cents: int,
        heartbeat_interval_s: int = V2_DEFAULT_HEARTBEAT_INTERVAL_S,
    ) -> uuid.UUID:
        """Create a task row in CREATED state. Returns the generated task_id."""
        task_id = uuid.uuid4()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO tasks
                       (task_id, owner_agent_id, state, payload, budget_cents,
                        heartbeat_interval_s)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        str(task_id),
                        owner_agent_id,
                        TaskState.CREATED.value,
                        json.dumps(payload),
                        budget_cents,
                        heartbeat_interval_s,
                    ),
                )
        logger.debug("Task created: %s for %s", task_id, owner_agent_id)
        return task_id

    def update_state(
        self,
        task_id: uuid.UUID,
        target_state: TaskState,
        reason: str | None = None,
    ) -> None:
        """Transition a task to a new state with validation.

        Raises InvalidTransition if the transition is not in the
        valid transitions map.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                current = self._get_current_state(cur, task_id)
                if current is None:
                    raise ValueError(f"Task {task_id} not found")

                if not validate_transition(current, target_state):
                    raise InvalidTransition(task_id, current, target_state)

                if reason and target_state in (
                    TaskState.STUCK,
                    TaskState.FAILED,
                    TaskState.FAILED_UNRECOVERABLE,
                ):
                    cur.execute(
                        "UPDATE tasks SET state = %s, stuck_reason = %s WHERE task_id = %s",
                        (target_state.value, reason, str(task_id)),
                    )
                else:
                    cur.execute(
                        "UPDATE tasks SET state = %s WHERE task_id = %s",
                        (target_state.value, str(task_id)),
                    )
        logger.debug("Task %s: %s -> %s", task_id, current.value, target_state.value)

    # ---------------------------------------------------------------
    # Claim and heartbeat
    # ---------------------------------------------------------------

    def claim_task(self, task_id: uuid.UUID, agent_id: str) -> None:
        """Agent claims a task. Transitions CREATED -> CLAIMED_BY_AGENT."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                current = self._get_current_state(cur, task_id)
                if current is None:
                    raise ValueError(f"Task {task_id} not found")
                if not validate_transition(current, TaskState.CLAIMED_BY_AGENT):
                    raise InvalidTransition(task_id, current, TaskState.CLAIMED_BY_AGENT)

                now = datetime.now(timezone.utc)
                cur.execute(
                    """UPDATE tasks
                       SET state = %s, owner_agent_id = %s, last_heartbeat_at = %s
                       WHERE task_id = %s""",
                    (TaskState.CLAIMED_BY_AGENT.value, agent_id, now, str(task_id)),
                )
        logger.debug("Task %s claimed by %s", task_id, agent_id)

    def heartbeat(self, task_id: uuid.UUID) -> None:
        """Record a heartbeat for an in-progress task."""
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE tasks SET last_heartbeat_at = %s
                       WHERE task_id = %s AND state = %s""",
                    (now, str(task_id), TaskState.IN_PROGRESS.value),
                )
                if cur.rowcount == 0:
                    logger.warning("Heartbeat for %s: not in_progress or not found", task_id)

    # ---------------------------------------------------------------
    # Stuck detection
    # ---------------------------------------------------------------

    def get_stuck_tasks(
        self,
        threshold_multiplier: float = V2_STUCK_THRESHOLD_MULTIPLIER,
    ) -> list[uuid.UUID]:
        """Find tasks that are in_progress but have not sent a heartbeat
        within threshold_multiplier * heartbeat_interval_s.

        Contact center heritage: this is the ACD "agent not ready" timer.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT task_id FROM tasks
                       WHERE state = %s
                         AND last_heartbeat_at IS NOT NULL
                         AND last_heartbeat_at < NOW() - (heartbeat_interval_s * %s * INTERVAL '1 second')
                    """,
                    (TaskState.IN_PROGRESS.value, threshold_multiplier),
                )
                return [uuid.UUID(row[0]) for row in cur.fetchall()]

    def mark_stuck(
        self,
        task_id: uuid.UUID,
        reason: str = "heartbeat timeout",
    ) -> None:
        """Mark an in-progress task as stuck."""
        self.update_state(task_id, TaskState.STUCK, reason=reason)
        logger.info("Task %s marked stuck: %s", task_id, reason)

    # ---------------------------------------------------------------
    # Takeover
    # ---------------------------------------------------------------

    def takeover(
        self,
        task_id: uuid.UUID,
        new_agent_id: str,
    ) -> None:
        """Reassign a stuck or failed task to a new agent.

        Records the previous owner, increments takeover count, and
        transitions to RESCHEDULED. The new agent must then claim it.

        Contact center heritage: this is the call transfer. The
        previous agent is recorded, the call re-enters the queue,
        and a new agent picks it up.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                current = self._get_current_state(cur, task_id)
                if current is None:
                    raise ValueError(f"Task {task_id} not found")
                if not validate_transition(current, TaskState.RESCHEDULED):
                    raise InvalidTransition(task_id, current, TaskState.RESCHEDULED)

                # Record previous owner
                cur.execute(
                    "SELECT owner_agent_id, takeover_count FROM tasks WHERE task_id = %s",
                    (str(task_id),),
                )
                row = cur.fetchone()
                prev_owner = row[0]
                takeover_count = (row[1] or 0) + 1

                cur.execute(
                    """UPDATE tasks
                       SET state = %s,
                           previous_owner_id = %s,
                           takeover_count = %s,
                           stuck_reason = NULL
                       WHERE task_id = %s""",
                    (
                        TaskState.RESCHEDULED.value,
                        prev_owner,
                        takeover_count,
                        str(task_id),
                    ),
                )
        logger.info(
            "Task %s takeover: %s -> %s (count: %d)",
            task_id, prev_owner, new_agent_id, takeover_count,
        )

    # ---------------------------------------------------------------
    # Verification
    # ---------------------------------------------------------------

    def verify_task(self, task_id: uuid.UUID) -> None:
        """Mark a completed task as verified."""
        self.update_state(task_id, TaskState.COMPLETED_VERIFIED)
        logger.debug("Task %s verified", task_id)
