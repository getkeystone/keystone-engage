"""Tests for the v2 task state machine.

Tests transition validation, terminal states, and the InvalidTransition
exception. No database required: these test the state machine logic only.
"""

import uuid

import pytest

from keystone_engage.substrate.models import (
    InvalidTransition,
    TaskState,
    VALID_TRANSITIONS,
    validate_transition,
)


class TestValidTransitions:
    """Test that the transition map encodes the correct state machine."""

    def test_created_to_claimed(self):
        assert validate_transition(TaskState.CREATED, TaskState.CLAIMED_BY_AGENT)

    def test_created_to_in_progress(self):
        """v1 compat: direct created -> in_progress."""
        assert validate_transition(TaskState.CREATED, TaskState.IN_PROGRESS)

    def test_created_to_failed(self):
        assert validate_transition(TaskState.CREATED, TaskState.FAILED)

    def test_claimed_to_in_progress(self):
        assert validate_transition(TaskState.CLAIMED_BY_AGENT, TaskState.IN_PROGRESS)

    def test_in_progress_to_completed(self):
        assert validate_transition(TaskState.IN_PROGRESS, TaskState.COMPLETED)

    def test_in_progress_to_stuck(self):
        assert validate_transition(TaskState.IN_PROGRESS, TaskState.STUCK)

    def test_stuck_to_rescheduled(self):
        assert validate_transition(TaskState.STUCK, TaskState.RESCHEDULED)

    def test_stuck_to_unrecoverable(self):
        assert validate_transition(TaskState.STUCK, TaskState.FAILED_UNRECOVERABLE)

    def test_rescheduled_to_claimed(self):
        """After takeover, new agent claims the task."""
        assert validate_transition(TaskState.RESCHEDULED, TaskState.CLAIMED_BY_AGENT)

    def test_completed_to_verified(self):
        assert validate_transition(TaskState.COMPLETED, TaskState.COMPLETED_VERIFIED)

    def test_failed_to_rescheduled(self):
        """Retry via takeover."""
        assert validate_transition(TaskState.FAILED, TaskState.RESCHEDULED)


class TestInvalidTransitions:
    """Test that invalid transitions are rejected."""

    def test_completed_to_in_progress(self):
        assert not validate_transition(TaskState.COMPLETED, TaskState.IN_PROGRESS)

    def test_verified_is_terminal(self):
        """completed_verified has no outgoing transitions."""
        for target in TaskState:
            assert not validate_transition(TaskState.COMPLETED_VERIFIED, target)

    def test_unrecoverable_is_terminal(self):
        """failed_unrecoverable has no outgoing transitions."""
        for target in TaskState:
            assert not validate_transition(TaskState.FAILED_UNRECOVERABLE, target)

    def test_in_progress_to_claimed(self):
        """Cannot go backward from in_progress to claimed."""
        assert not validate_transition(TaskState.IN_PROGRESS, TaskState.CLAIMED_BY_AGENT)

    def test_stuck_to_in_progress(self):
        """Stuck tasks must be rescheduled, not resumed."""
        assert not validate_transition(TaskState.STUCK, TaskState.IN_PROGRESS)


class TestInvalidTransitionException:
    def test_exception_message(self):
        tid = uuid.uuid4()
        exc = InvalidTransition(tid, TaskState.COMPLETED, TaskState.IN_PROGRESS)
        assert str(tid) in str(exc)
        assert "completed" in str(exc)
        assert "in_progress" in str(exc)

    def test_exception_fields(self):
        tid = uuid.uuid4()
        exc = InvalidTransition(tid, TaskState.STUCK, TaskState.COMPLETED)
        assert exc.task_id == tid
        assert exc.current == TaskState.STUCK
        assert exc.target == TaskState.COMPLETED


class TestTransitionCompleteness:
    """Every TaskState must have an entry in VALID_TRANSITIONS."""

    def test_all_states_have_transition_entry(self):
        for state in TaskState:
            assert state in VALID_TRANSITIONS, f"{state.value} missing from VALID_TRANSITIONS"

    def test_terminal_states_have_empty_transitions(self):
        terminals = [TaskState.COMPLETED_VERIFIED, TaskState.FAILED_UNRECOVERABLE]
        for state in terminals:
            assert VALID_TRANSITIONS[state] == set(), f"{state.value} should be terminal"
