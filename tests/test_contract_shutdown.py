"""WP0-D contract tests: cooperative cancellation and shutdown budgets.

Pure state and arithmetic tests. No thread, no process, no Qt object, no wait.
"""

from __future__ import annotations

import pytest

from castlearq_desktop.core.contracts import (
    FORCED_TERMINATION_TIMEOUT_SECONDS,
    GRACEFUL_TERMINATION_TIMEOUT_SECONDS,
    TOTAL_SHUTDOWN_BUDGET_SECONDS,
    UI_THREAD_SYNCHRONOUS_WAIT_PERMITTED,
    WORKER_THREAD_FORCE_KILL_PERMITTED,
    CancellationState,
    ShutdownPhase,
    ShutdownState,
    next_shutdown_action,
    phase_for_elapsed,
    remaining_forced_budget,
    remaining_graceful_budget,
    shutdown_budget_exhausted,
    shutdown_failed,
    should_continue_after_request,
    should_issue_request,
)


def test_approved_shutdown_budget_constants() -> None:
    """The DEC-F budgets must match the approved values exactly."""
    assert GRACEFUL_TERMINATION_TIMEOUT_SECONDS == 2.0
    assert FORCED_TERMINATION_TIMEOUT_SECONDS == 3.0
    assert TOTAL_SHUTDOWN_BUDGET_SECONDS == 5.0


def test_prohibited_mechanisms_are_recorded_as_prohibited() -> None:
    """Force-killing a worker and blocking the UI thread are never permitted."""
    assert WORKER_THREAD_FORCE_KILL_PERMITTED is False
    assert UI_THREAD_SYNCHRONOUS_WAIT_PERMITTED is False


# --- Cooperative cancellation (DEC-F) -------------------------------------


def test_requests_are_permitted_before_cancellation() -> None:
    state = CancellationState()
    assert not state.is_cancelled
    assert should_issue_request(state)
    assert should_continue_after_request(state)


def test_cancellation_stops_further_requests() -> None:
    """After cancellation no further request may be issued."""
    state = CancellationState().cancel()
    assert state.is_cancelled
    assert not should_issue_request(state)


def test_cancellation_is_cooperative_and_immutable() -> None:
    """Cancelling returns a new state; the original is untouched."""
    original = CancellationState()
    cancelled = original.cancel()
    assert cancelled is not original
    assert not original.is_cancelled
    assert cancelled.is_cancelled


def test_cancellation_after_request_also_stops_work() -> None:
    """The post-request check must observe cancellation requested mid-flight."""
    state = CancellationState()
    assert should_continue_after_request(state)
    state = state.cancel()
    assert not should_continue_after_request(state)


# --- Shutdown state transitions (DEC-F) -----------------------------------


def test_idle_state_does_not_suppress_startup() -> None:
    state = ShutdownState()
    assert state.phase is ShutdownPhase.IDLE
    assert not state.startup_suppressed


def test_beginning_shutdown_suppresses_new_startup() -> None:
    """New startup/readiness work is suppressed while shutdown is in progress."""
    state = ShutdownState().begin()
    assert state.phase is ShutdownPhase.GRACEFUL
    assert state.startup_suppressed


def test_escalation_only_from_graceful_to_forced() -> None:
    graceful = ShutdownState().begin()
    forced = graceful.escalate()
    assert forced.phase is ShutdownPhase.FORCED
    assert forced.startup_suppressed


def test_escalation_is_a_no_op_outside_the_graceful_phase() -> None:
    """Escalation must not skip phases or act on a terminal state."""
    confirmed = ShutdownState().confirm()
    assert confirmed.escalate().phase is ShutdownPhase.CONFIRMED


def test_confirmed_and_failed_are_terminal_and_release_suppression() -> None:
    """A terminal outcome ends the in-progress state."""
    confirmed = ShutdownState().begin().confirm()
    assert confirmed.phase is ShutdownPhase.CONFIRMED
    assert not confirmed.startup_suppressed

    failed = ShutdownState().begin().fail()
    assert failed.phase is ShutdownPhase.FAILED
    assert not failed.startup_suppressed


# --- Shutdown budget arithmetic (DEC-F) -----------------------------------


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(0.0, ShutdownPhase.GRACEFUL), (1.0, ShutdownPhase.GRACEFUL),
     (1.999, ShutdownPhase.GRACEFUL)],
)
def test_within_graceful_budget(elapsed: float, expected: ShutdownPhase) -> None:
    assert phase_for_elapsed(elapsed, exited=False) is expected


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [(2.0, ShutdownPhase.FORCED), (3.0, ShutdownPhase.FORCED),
     (4.999, ShutdownPhase.FORCED)],
)
def test_within_forced_budget(elapsed: float, expected: ShutdownPhase) -> None:
    assert phase_for_elapsed(elapsed, exited=False) is expected


@pytest.mark.parametrize("elapsed", [5.0, 6.0, 100.0])
def test_budget_exhausted_without_exit_is_failure(elapsed: float) -> None:
    """Beyond 5 s without a confirmed exit, shutdown has failed."""
    assert phase_for_elapsed(elapsed, exited=False) is ShutdownPhase.FAILED


@pytest.mark.parametrize("elapsed", [0.1, 2.0, 4.9])
def test_exit_at_any_point_confirms_shutdown(elapsed: float) -> None:
    """A confirmed exit within budget is a success, never a failure."""
    assert phase_for_elapsed(elapsed, exited=True) is ShutdownPhase.CONFIRMED
    assert not shutdown_failed(elapsed, exited=True)


def test_remaining_graceful_budget_is_clamped() -> None:
    assert remaining_graceful_budget(0.0) == 2.0
    assert remaining_graceful_budget(1.5) == 0.5
    assert remaining_graceful_budget(5.0) == 0.0


def test_remaining_forced_budget_is_clamped() -> None:
    assert remaining_forced_budget(0.0) == 5.0
    assert remaining_forced_budget(3.0) == 2.0
    assert remaining_forced_budget(9.0) == 0.0


def test_budget_exhaustion_boundary() -> None:
    """Exhaustion begins exactly at the total budget."""
    assert not shutdown_budget_exhausted(4.999)
    assert shutdown_budget_exhausted(5.0)


def test_in_budget_wait_is_not_yet_a_failure() -> None:
    """A process still alive inside the budget is not a shutdown failure."""
    assert not shutdown_failed(4.9, exited=False)
    assert shutdown_failed(5.0, exited=False)


def test_next_action_sequence_follows_the_approved_order() -> None:
    """Normal termination first, then forced, then a failure report."""
    assert next_shutdown_action(0.0, exited=False) == "request_normal_termination"
    assert next_shutdown_action(2.5, exited=False) == "request_forced_termination"
    assert next_shutdown_action(5.0, exited=False) == "report_shutdown_failed"
    assert next_shutdown_action(0.1, exited=True) is None


def test_negative_elapsed_is_refused() -> None:
    """A negative elapsed time is nonsensical and must not be accepted."""
    with pytest.raises(ValueError):
        phase_for_elapsed(-1.0, exited=False)
