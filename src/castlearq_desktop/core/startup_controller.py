# Copyright 2026 Nicolas Bruna
#
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Application-level startup/readiness/shutdown controller (ADR-0002 D7).

``StartupController`` is a ``QObject`` that orchestrates the existing
``CoreStartupCoordinator`` (launch + asynchronous readiness) and the existing
``LifecycleManager`` (process shutdown), and exposes Qt signals for the UI.

Responsibilities it owns (and only these):

* a single active startup attempt (duplicate concurrent starts are suppressed);
* retention of the readiness checker for the whole asynchronous operation and
  suppression of stale results from a superseded attempt;
* propagation of lifecycle state and readiness outcomes to the UI via signals;
* a non-blocking, coalesced, application-level 5-second shutdown deadline that
  is independent of wall-clock time and does not reset on repeated requests;
* translation of the terminal shutdown outcome for the composition root.

It reuses existing components and duplicates no subprocess, HTTP, lifecycle, or
shutdown policy. Process shutdown is delegated to ``coordinator.manager.stop()``
and escalation is driven by the existing ``LifecycleObserver``/``observe()``.

DEC-A admission stays fail-closed: this controller never supplies an admission
source, never invents compatibility criteria, and never turns readiness into a
success unless the existing approved contract already permits it.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal

from castlearq_desktop.core.contracts import TOTAL_SHUTDOWN_BUDGET_SECONDS
from castlearq_desktop.core.lifecycle_manager import LifecycleState

#: Nominal UI/coordination tick, in milliseconds (matches the observation
#: cadence ratified in ADR-0002 D7). Read-only sampling of manager state.
_TICK_INTERVAL_MS = 100


class MonotonicClock:
    """Minimal production monotonic clock satisfying the ``Clock`` protocol."""

    def monotonic(self) -> float:
        return time.monotonic()


def _create_qtimer() -> Any:
    """Create a real ``QTimer`` with a lazy PySide6 import."""
    from PySide6.QtCore import QTimer

    return QTimer()


class StartupController(QObject):
    """Coordinate Core startup, readiness, and shutdown for the UI.

    Signals
    -------
    state_changed(object)
        A ``LifecycleState`` whenever the observable manager state changes.
    readiness_result(object)
        The terminal ``ReadinessWaitResult`` of the active attempt.
    readiness_error(object)
        An unexpected exception surfaced by the readiness checker.
    shutdown_finished(object)
        The terminal ``LifecycleState`` (``STOPPED`` = confirmed exit,
        ``SHUTDOWN_FAILED`` = unconfirmed exit at the deadline). Emitted once.
    """

    state_changed = Signal(object)
    readiness_result = Signal(object)
    readiness_error = Signal(object)
    shutdown_finished = Signal(object)

    def __init__(
        self,
        coordinator: Any,
        *,
        timer_factory: Optional[Any] = None,
        parent: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self._coordinator = coordinator
        # Share the coordinator's monotonic clock so the application-level
        # shutdown deadline and the readiness budget use one time source
        # (ADR-0002 D7). Tests inject a deterministic clock via the coordinator.
        self._clock = coordinator.clock
        self._timer_factory = timer_factory or _create_qtimer

        self._active_checker: Optional[Any] = None
        self._starting = False
        self._shutting_down = False
        self._shutdown_terminal = False
        self._last_state: Optional[LifecycleState] = None

        self._tick_timer: Optional[Any] = None
        self._shutdown_deadline_timer: Optional[Any] = None

    @property
    def shutdown_complete(self) -> bool:
        """Whether a terminal shutdown outcome has already been emitted."""
        return self._shutdown_terminal

    # -- startup ------------------------------------------------------------
    def start(self) -> Any:
        """Request a single Core startup attempt.

        Returns the coordinator's ``OperationResult`` or ``None`` when a
        duplicate concurrent start (or a start during shutdown) is suppressed.
        """
        if self._starting or self._shutting_down:
            return None
        self._starting = True
        result = self._coordinator.launch()
        manager = self._coordinator.manager
        self._emit_state(manager.state if manager is not None else None)
        if getattr(result, "status", None) in ("ok", "pending"):
            self._begin_readiness()
            self._start_tick()
        else:
            # Definite launch failure: no readiness, no observation to start.
            self._starting = False
        return result

    def _begin_readiness(self) -> None:
        # Defensive: never keep more than one live checker.
        if self._active_checker is not None:
            self._active_checker.stop()
            self._active_checker = None
        checker = self._coordinator.check_readiness_async()
        self._active_checker = checker
        # Bind to this specific checker instance so a stale completion from a
        # superseded attempt can be identified and ignored.
        checker.result.connect(
            lambda r, c=checker: self._on_readiness_result(r, c))
        checker.error.connect(
            lambda e, c=checker: self._on_readiness_error(e, c))

    def _on_readiness_result(self, result: Any, checker: Any) -> None:
        if self._shutting_down:
            return  # a cancelled/late completion during shutdown is ignored
        if checker is not self._active_checker:
            return  # stale: from a superseded attempt
        self._starting = False
        self.readiness_result.emit(result)

    def _on_readiness_error(self, exc: Exception, checker: Any) -> None:
        if self._shutting_down:
            return
        if checker is not self._active_checker:
            return
        self._starting = False
        self.readiness_error.emit(exc)

    # -- state tick ---------------------------------------------------------
    def _start_tick(self) -> None:
        if self._tick_timer is None:
            self._tick_timer = self._timer_factory()
            self._tick_timer.setInterval(_TICK_INTERVAL_MS)
            self._tick_timer.timeout.connect(self._on_tick)
        is_active = getattr(self._tick_timer, "isActive", None)
        if callable(is_active):
            if not is_active():
                self._tick_timer.start()
        else:
            self._tick_timer.start()

    def _on_tick(self) -> None:
        manager = self._coordinator.manager
        if manager is None:
            return
        state = manager.state
        self._emit_state(state)
        if self._shutting_down and not self._shutdown_terminal and state in (
            LifecycleState.STOPPED,
            LifecycleState.SHUTDOWN_FAILED,
        ):
            self._finalize_shutdown(state)

    def _emit_state(self, state: Optional[LifecycleState]) -> None:
        if state is not None and state is not self._last_state:
            self._last_state = state
            self.state_changed.emit(state)


    # -- shutdown -----------------------------------------------------------
    def request_shutdown(self) -> None:
        """Accept the first shutdown request; coalesce the rest.

        Starts exactly one application-level 5-second deadline and delegates
        process shutdown to the existing lifecycle manager. Never blocks.
        """
        if self._shutdown_terminal:
            return
        if self._shutting_down:
            return  # coalesced: the deadline is not reset
        self._shutting_down = True

        # Cancel pending readiness first so a cancelled completion can never be
        # reported as success. Clear the reference before stop() so the
        # resulting cancelled emission is treated as stale.
        checker = self._active_checker
        self._active_checker = None
        if checker is not None:
            checker.stop()

        manager = self._coordinator.manager
        if manager is None:
            # No Core was ever started: nothing to shut down.
            self._finalize_shutdown(LifecycleState.STOPPED)
            return

        self._start_tick()

        # One application-level deadline from the first accepted request.
        self._shutdown_deadline_timer = self._timer_factory()
        if hasattr(self._shutdown_deadline_timer, "setSingleShot"):
            self._shutdown_deadline_timer.setSingleShot(True)
        self._shutdown_deadline_timer.setInterval(
            int(TOTAL_SHUTDOWN_BUDGET_SECONDS * 1000))
        self._shutdown_deadline_timer.timeout.connect(self._on_shutdown_deadline)
        self._shutdown_deadline_timer.start()

        # Delegate process shutdown; do not duplicate termination/escalation.
        manager.stop()
        self._emit_state(manager.state)
        if manager.state in (LifecycleState.STOPPED, LifecycleState.SHUTDOWN_FAILED):
            self._finalize_shutdown(manager.state)

    def _on_shutdown_deadline(self) -> None:
        if self._shutdown_terminal:
            return
        manager = self._coordinator.manager
        state = manager.state if manager is not None else None
        if state is LifecycleState.STOPPED:
            self._finalize_shutdown(state)  # confirmed just before the deadline
        else:
            # Unconfirmed exit at the deadline: terminal and irreversible. No
            # additional termination signal is sent here.
            self._finalize_shutdown(LifecycleState.SHUTDOWN_FAILED)

    def _finalize_shutdown(self, state: LifecycleState) -> None:
        if self._shutdown_terminal:
            return
        self._shutdown_terminal = True
        self._stop_timer(self._shutdown_deadline_timer)
        self._shutdown_deadline_timer = None
        self._stop_timer(self._tick_timer)
        self._tick_timer = None
        self._emit_state(state)
        self.shutdown_finished.emit(state)

    @staticmethod
    def _stop_timer(timer: Optional[Any]) -> None:
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass


__all__ = ["MonotonicClock", "StartupController"]
