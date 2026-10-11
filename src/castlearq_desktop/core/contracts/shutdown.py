"""WP0-D — Cooperative cancellation and shutdown decision helpers (pure).

Implements the DEC-F decision rules as pure functions and small immutable state
values. No thread is created, no process is started or terminated, no Qt object
is constructed, and no real time is waited.

Deliberately **not** implemented here: worker threads, signals, real
``terminate()``/``kill()`` mechanics, and Qt-thread lifetime handling. Those
belong to a later work package.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

#: Graceful-termination budget, in seconds (DEC-F).
GRACEFUL_TERMINATION_TIMEOUT_SECONDS = 2.0

#: Forced-termination budget, in seconds, *additional* to the graceful phase.
FORCED_TERMINATION_TIMEOUT_SECONDS = 3.0

#: Total shutdown budget: graceful + forced (DEC-F).
TOTAL_SHUTDOWN_BUDGET_SECONDS = (
    GRACEFUL_TERMINATION_TIMEOUT_SECONDS + FORCED_TERMINATION_TIMEOUT_SECONDS
)

#: A worker thread is never force-killed (DEC-F).
WORKER_THREAD_FORCE_KILL_PERMITTED = False

#: The Qt main thread never blocks waiting for a worker or shutdown (DEC-F).
UI_THREAD_SYNCHRONOUS_WAIT_PERMITTED = False


class ShutdownPhase(Enum):
    """Ordered phases of the approved cooperative shutdown sequence."""

    IDLE = "idle"
    GRACEFUL = "graceful"
    FORCED = "forced"
    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass(frozen=True)
class CancellationState:
    """Cooperative cancellation flag; immutable and safe to copy."""

    cancelled: bool = False

    def cancel(self) -> "CancellationState":
        """Return a new state with cancellation requested."""
        return replace(self, cancelled=True)

    @property
    def is_cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self.cancelled


def should_issue_request(state: CancellationState) -> bool:
    """Whether a new HTTP request may be issued (checked *before* each request).

    Cancellation is cooperative: once requested, no further request is issued.
    """
    return not state.is_cancelled


def should_continue_after_request(state: CancellationState) -> bool:
    """Whether work may continue *after* the current request returns (DEC-F)."""
    return not state.is_cancelled


@dataclass(frozen=True)
class ShutdownState:
    """Immutable shutdown state, including startup suppression.

    While ``in_progress`` is true, new startup/readiness operations are
    suppressed (DEC-F).
    """

    phase: ShutdownPhase = ShutdownPhase.IDLE
    in_progress: bool = False

    @property
    def startup_suppressed(self) -> bool:
        """Whether new startup/readiness operations must be refused."""
        return self.in_progress

    def begin(self) -> "ShutdownState":
        """Enter the graceful phase; suppresses new startup work."""
        return ShutdownState(phase=ShutdownPhase.GRACEFUL, in_progress=True)

    def escalate(self) -> "ShutdownState":
        """Move from graceful to forced termination."""
        if self.phase is ShutdownPhase.GRACEFUL:
            return ShutdownState(phase=ShutdownPhase.FORCED, in_progress=True)
        return self

    def confirm(self) -> "ShutdownState":
        """Record that process exit was confirmed."""
        return ShutdownState(phase=ShutdownPhase.CONFIRMED, in_progress=False)

    def fail(self) -> "ShutdownState":
        """Record that termination could not be confirmed (``SHUTDOWN_FAILED``)."""
        return ShutdownState(phase=ShutdownPhase.FAILED, in_progress=False)


def phase_for_elapsed(elapsed: float, exited: bool) -> ShutdownPhase:
    """Return the phase implied by elapsed time and whether the process exited.

    Rules (DEC-F):

    * the process exited at any point → :attr:`ShutdownPhase.CONFIRMED`;
    * ``0 <= elapsed < 2 s`` → still in the graceful phase;
    * ``2 s <= elapsed < 5 s`` → in the forced phase;
    * ``elapsed >= 5 s`` without exit → :attr:`ShutdownPhase.FAILED`.
    """
    if exited:
        return ShutdownPhase.CONFIRMED
    if elapsed < 0:
        raise ValueError("elapsed must not be negative")
    if elapsed < GRACEFUL_TERMINATION_TIMEOUT_SECONDS:
        return ShutdownPhase.GRACEFUL
    if elapsed < TOTAL_SHUTDOWN_BUDGET_SECONDS:
        return ShutdownPhase.FORCED
    return ShutdownPhase.FAILED


def remaining_graceful_budget(elapsed: float) -> float:
    """Seconds left in the graceful phase; never negative."""
    return max(0.0, GRACEFUL_TERMINATION_TIMEOUT_SECONDS - elapsed)


def remaining_forced_budget(elapsed: float) -> float:
    """Seconds left in the forced phase; never negative."""
    return max(0.0, TOTAL_SHUTDOWN_BUDGET_SECONDS - elapsed)


def shutdown_budget_exhausted(elapsed: float) -> bool:
    """Whether the total graceful+forced budget is exhausted."""
    return elapsed >= TOTAL_SHUTDOWN_BUDGET_SECONDS


def shutdown_failed(elapsed: float, exited: bool) -> bool:
    """Whether shutdown must be reported as failed (DEC-F).

    Failure means the budget was exhausted without a confirmed exit. A still
    in-budget wait is not yet a failure.
    """
    if exited:
        return False
    return shutdown_budget_exhausted(elapsed)


def next_shutdown_action(elapsed: float, exited: bool) -> Optional[str]:
    """Describe the next approved action, or ``None`` at a terminal state.

    Purely descriptive: performs no action and signals nothing.
    """
    phase = phase_for_elapsed(elapsed, exited)
    if phase is ShutdownPhase.CONFIRMED:
        return None
    if phase is ShutdownPhase.GRACEFUL:
        return "request_normal_termination"
    if phase is ShutdownPhase.FORCED:
        return "request_forced_termination"
    return "report_shutdown_failed"


__all__ = [
    "FORCED_TERMINATION_TIMEOUT_SECONDS",
    "GRACEFUL_TERMINATION_TIMEOUT_SECONDS",
    "TOTAL_SHUTDOWN_BUDGET_SECONDS",
    "UI_THREAD_SYNCHRONOUS_WAIT_PERMITTED",
    "WORKER_THREAD_FORCE_KILL_PERMITTED",
    "CancellationState",
    "ShutdownPhase",
    "ShutdownState",
    "next_shutdown_action",
    "phase_for_elapsed",
    "remaining_forced_budget",
    "remaining_graceful_budget",
    "shutdown_budget_exhausted",
    "shutdown_failed",
    "should_continue_after_request",
    "should_issue_request",
]
