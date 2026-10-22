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

"""WP2B — Core process lifecycle manager (contract C1–C8)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from castlearq_desktop.core.contracts.errors import CastleArqError, ErrorCode
from castlearq_desktop.core.contracts.shutdown import (
    FORCED_TERMINATION_TIMEOUT_SECONDS,
    GRACEFUL_TERMINATION_TIMEOUT_SECONDS,
    TOTAL_SHUTDOWN_BUDGET_SECONDS,
    ShutdownPhase,
)


class LifecycleState(Enum):
    NOT_STARTED = "not_started"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    START_FAILED = "start_failed"
    UNEXPECTED_EXIT = "unexpected_exit"
    SHUTDOWN_FAILED = "shutdown_failed"


class ProcessHandle(Protocol):
    def is_alive(self) -> bool: ...
    def poll(self) -> Optional[int]: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


class ProcessAdapter(Protocol):
    def spawn(self, argv: List[str], *, shell: bool) -> ProcessHandle: ...


class Clock(Protocol):
    def monotonic(self) -> float: ...


class ProcessSpawnError(Exception):
    """Definite failure: adapter guarantees no child was created."""


class ProcessOwnershipUncertainError(ProcessSpawnError):
    def __init__(self, message: str = "indeterminate creation.",
                 partial_handle: Optional[ProcessHandle] = None) -> None:
        super().__init__(message)
        self.partial_handle = partial_handle


def build_launch_args(validated_python: str) -> List[str]:
    if not isinstance(validated_python, str) or not validated_python.strip():
        raise ValueError("An interpreter path must be a non-empty string.")
    if not os.path.isabs(validated_python):
        raise ValueError("An interpreter path must be absolute.")
    return [validated_python, "-m", "castlearq.main", "serve"]


RESULT_STATUSES = ("ok", "refused", "coalesced", "failed", "pending")


@dataclass
class OperationResult:
    status: str
    state: LifecycleState
    error: Optional[CastleArqError] = None
    startup_suppressed: bool = False
    detail: str = ""
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LifecycleManager:
    adapter: ProcessAdapter
    clock: Clock
    state: LifecycleState = LifecycleState.NOT_STARTED
    shutdown_phase: ShutdownPhase = ShutdownPhase.IDLE
    ownership_unresolved: bool = False
    shutdown_failed_reported: bool = False
    _handle: Optional[ProcessHandle] = field(default=None, repr=False)
    _stop_requested_at: Optional[float] = field(default=None, repr=False)
    _kill_sent: bool = field(default=False, repr=False)
    _last_exit_code: Optional[int] = field(default=None, repr=False)
    _last_pid: Optional[int] = field(default=None, repr=False)

    @property
    def has_owned_handle(self) -> bool:
        return self._handle is not None

    @property
    def last_exit_code(self) -> Optional[int]:
        """Exit code of the last observed process termination, or ``None``."""
        return self._last_exit_code

    @property
    def last_pid(self) -> Optional[int]:
        """OS process ID of the last observed child, or ``None`` when unavailable."""
        return self._last_pid

    def _failed(self, state: LifecycleState, code: ErrorCode,
                message: str, **context: Any) -> OperationResult:
        self.state = state
        return OperationResult(status="failed", state=state,
                               error=CastleArqError(code, message, dict(context)),
                               detail=message, context=dict(context))

    def _refused(self, message: str, **context: Any) -> OperationResult:
        suppressed = self.state is LifecycleState.STOPPING
        return OperationResult(status="refused", state=self.state, error=None,
                               startup_suppressed=suppressed,
                               detail=message, context=dict(context))

    def _probe_status(self) -> str:
        """Return "exited", "alive", or "unknown" without blocking.

        Unknown means both status probes failed; the caller must not
        assume the process exited nor discard the owned handle.
        """
        handle = self._handle
        if handle is None:
            return "unknown"
        try:
            code = handle.poll()
        except Exception:
            try:
                alive = bool(handle.is_alive())
            except Exception:
                return "unknown"
            return "alive" if alive else "exited"
        if code is not None:
            return "exited"
        try:
            if not handle.is_alive():
                return "exited"
        except Exception:
            return "unknown"
        return "alive"

    def _observe_status(self) -> tuple:
        """Return a tri-state observation: ("exited"|"alive"|"unknown", code).

        A single status probe is performed per call; the exit code is only
        re-read when the status is confirmed "exited". "unknown" means
        neither liveness nor termination is confirmed.
        """
        status = self._probe_status()
        if status != "exited":
            return status, None
        # Re-read the exit code when available; never raises.
        try:
            code = self._handle.poll()  # type: ignore[union-attr]
        except Exception:
            return status, None
        if code is not None:
            return status, code
        return status, None

    def _handle_exited(self) -> tuple:
        status, code = self._observe_status()
        if status == "exited":
            return True, code
        return False, None

    def start(self, validated_python: str) -> OperationResult:
        if self.ownership_unresolved:
            return self._refused("Unresolved ownership; starts refused.",
                                 ownership_unresolved=True)
        if self.state in (LifecycleState.STARTING, LifecycleState.RUNNING):
            return self._refused("Duplicate start refused.", duplicate=True)
        if self.state is LifecycleState.STOPPING:
            res = self._refused("Start refused during shutdown.",
                                startup_suppressed=True)
            res.startup_suppressed = True
            return res
        if self.state is LifecycleState.SHUTDOWN_FAILED:
            return self._refused("Failed-shutdown handle retained.",
                                 retained_ownership=True)
        if self.state is LifecycleState.START_FAILED and self._handle is not None:
            return self._refused("Failed-start handle retained.",
                                 retained_ownership=True)
        if self.state is LifecycleState.UNEXPECTED_EXIT:
            if self._handle is not None:
                return self._refused("Discard exited handle first.",
                                     retained_ownership=True)
            self._last_exit_code = None
            self._last_pid = None
        if self.state is LifecycleState.START_FAILED:
            self._handle = None
        try:
            argv = build_launch_args(validated_python)
        except ValueError as exc:
            return self._failed(LifecycleState.START_FAILED,
                                ErrorCode.INTERPRETER_INVALID,
                                "Interpreter path is not absolute.",
                                reason=str(exc))
        self.state = LifecycleState.STARTING
        try:
            handle = self.adapter.spawn(argv, shell=False)
        except ProcessOwnershipUncertainError as exc:
            partial = exc.partial_handle
            if partial is not None:
                self._handle = partial
                self.ownership_unresolved = False
                return self._adopt_and_observe()
            self._handle = None
            self.ownership_unresolved = True
            self.state = LifecycleState.START_FAILED
            return OperationResult(
                status="failed", state=self.state,
                error=CastleArqError(
                    ErrorCode.PROCESS_START_FAILED,
                    "Creation indeterminate; ownership unresolved.",
                    {"ownership_unresolved": True}),
                detail="Indeterminate creation.",
                context={"ownership_unresolved": True})
        except ProcessSpawnError as exc:
            self._handle = None
            return self._failed(LifecycleState.START_FAILED,
                                ErrorCode.PROCESS_START_FAILED,
                                "Core process creation failed.",
                                cause_message=str(exc))
        except Exception as exc:
            # Unknown outcome: the adapter did not guarantee that no child
            # was created, so treat as indeterminate and block new starts.
            self._handle = None
            self.ownership_unresolved = True
            self.state = LifecycleState.START_FAILED
            message = "Core process creation outcome is indeterminate."
            context = {"ownership_unresolved": True,
                       "cause_type": type(exc).__name__,
                       "cause_message": str(exc)}
            return OperationResult(
                status="failed", state=self.state,
                error=CastleArqError(ErrorCode.PROCESS_START_FAILED, message,
                                     dict(context)),
                detail=message, context=context)
        if handle is None:
            self._handle = None
            self.ownership_unresolved = True
            self.state = LifecycleState.START_FAILED
            return OperationResult(
                status="failed", state=self.state,
                error=CastleArqError(
                    ErrorCode.PROCESS_START_FAILED,
                    "No handle returned; ownership unresolved.",
                    {"ownership_unresolved": True}),
                detail="No handle.", context={"ownership_unresolved": True})
        self._handle = handle
        self.ownership_unresolved = False
        return self._adopt_and_observe()

    def _adopt_and_observe(self) -> OperationResult:
        status, code = self._observe_status()
        if status == "exited":
            self._last_exit_code = code
            self._last_pid = getattr(self._handle, "pid", None)
            self.state = LifecycleState.UNEXPECTED_EXIT
            self.shutdown_phase = ShutdownPhase.IDLE
            return OperationResult(status="failed", state=self.state, error=None,
                                   detail="Exited immediately after launch.",
                                   context={"immediate_exit": True,
                                            "exit_code": code,
                                            "pid": self._last_pid})
        if status == "unknown":
            self.state = LifecycleState.STARTING
            return OperationResult(status="pending", state=self.state,
                                   error=None,
                                   detail="Startup pending; status unknown.",
                                   context={"pending_startup": True})
        self.state = LifecycleState.RUNNING
        return OperationResult(status="ok", state=self.state, error=None,
                               detail="Started and owned.",
                               context={"adopted": True})

    def discard_terminated_handle(self) -> bool:
        if (self.state is LifecycleState.UNEXPECTED_EXIT
                and self._handle is not None
                and self._handle_exited()[0]):
            self._handle = None
            self._last_exit_code = None
            return True
        return False

    def stop(self) -> OperationResult:
        if self.ownership_unresolved:
            self.state = LifecycleState.START_FAILED
            return OperationResult(
                status="failed", state=self.state,
                error=CastleArqError(
                    ErrorCode.SHUTDOWN_FAILED,
                    "Cannot stop with unresolved ownership.",
                    {"ownership_unresolved": True}),
                detail="No handle.", context={"ownership_unresolved": True})
        if self._handle is None:
            if self.state is LifecycleState.STOPPING:
                return OperationResult(status="coalesced", state=self.state,
                                       detail="Coalesced.",
                                       context={})
            self.state = LifecycleState.STOPPED
            self.shutdown_phase = ShutdownPhase.IDLE
            self._stop_requested_at = None
            return OperationResult(status="ok", state=self.state,
                                   detail="Nothing to stop.",
                                   context={"idempotent": True})
        if self.state is LifecycleState.STOPPING:
            return OperationResult(
                status="coalesced", state=self.state,
                detail="Coalesced.",
                context={"budget_restarted": False, "resignalled": False})
        if self.state is LifecycleState.SHUTDOWN_FAILED:
            return OperationResult(status="refused", state=self.state,
                                   detail="No re-signal.",
                                   context={"resignalled": False})
        status = self._probe_status()
        if status == "exited":
            _, code = self._handle_exited()
            self._last_exit_code = code
            self._handle = None
            self.state = LifecycleState.STOPPED
            self.shutdown_phase = ShutdownPhase.CONFIRMED
            self._stop_requested_at = None
            return OperationResult(status="ok", state=self.state,
                                   detail="Already exited.",
                                   context={"exit_code": code})
        if status == "unknown":
            # Conservative: status probes failed, so the process may or may
            # not have exited. Do not signal, do not discard the handle, and
            # do not claim shutdown. Remain RUNNING/STARTING; a later
            # observation may resolve the status.
            return OperationResult(
                status="pending", state=self.state, error=None,
                detail="Process status unknown; handle retained.",
                context={"status_unknown": True})
        self.state = LifecycleState.STOPPING
        self.shutdown_phase = ShutdownPhase.GRACEFUL
        self._stop_requested_at = self.clock.monotonic()
        self._kill_sent = False
        try:
            self._handle.terminate()
        except Exception as exc:
            self.state = LifecycleState.SHUTDOWN_FAILED
            self.shutdown_phase = ShutdownPhase.FAILED
            self.shutdown_failed_reported = True
            message = "Graceful termination failed."
            context = {"cause_type": type(exc).__name__,
                       "cause_message": str(exc)}
            return OperationResult(
                status="failed", state=self.state,
                error=CastleArqError(ErrorCode.SHUTDOWN_FAILED, message,
                                     dict(context)),
                detail=message, context=context)
        return OperationResult(
            status="pending", state=self.state,
            detail="Shutdown started.",
            context={"shutdown_phase": self.shutdown_phase.value,
                     "graceful_timeout": GRACEFUL_TERMINATION_TIMEOUT_SECONDS,
                     "total_budget": TOTAL_SHUTDOWN_BUDGET_SECONDS})

    def observe(self) -> None:
        if self.ownership_unresolved:
            return
        handle = self._handle
        if handle is None:
            return
        status, code = self._observe_status()
        if status == "exited":
            self._last_exit_code = code
            self._last_pid = getattr(handle, "pid", None)
            self._handle = None
            self._stop_requested_at = None
            if self.state is LifecycleState.STOPPING:
                self.state = LifecycleState.STOPPED
                self.shutdown_phase = ShutdownPhase.CONFIRMED
                return
            if self.state is LifecycleState.SHUTDOWN_FAILED:
                self.state = LifecycleState.STOPPED
                self.shutdown_phase = ShutdownPhase.CONFIRMED
                self.shutdown_failed_reported = True
                return
            if self.state in (LifecycleState.STARTING, LifecycleState.RUNNING):
                self.state = LifecycleState.UNEXPECTED_EXIT
                self.shutdown_phase = ShutdownPhase.IDLE
                return
            return
        # Unknown status: probes failed, so neither exit nor aliveness is
        # confirmed. Never escalate, signal, or exhaust the budget here.
        if status == "unknown":
            return
        if self.state is LifecycleState.STARTING:
            self.state = LifecycleState.RUNNING
            return
        if self.state is not LifecycleState.STOPPING:
            return
        if self._stop_requested_at is None:
            return
        elapsed = self.clock.monotonic() - self._stop_requested_at
        if elapsed >= TOTAL_SHUTDOWN_BUDGET_SECONDS:
            self.state = LifecycleState.SHUTDOWN_FAILED
            self.shutdown_phase = ShutdownPhase.FAILED
            self.shutdown_failed_reported = True
            return
        if (elapsed >= GRACEFUL_TERMINATION_TIMEOUT_SECONDS
                and self.shutdown_phase is ShutdownPhase.GRACEFUL
                and not self._kill_sent):
            try:
                handle.kill()
            except Exception:
                self.state = LifecycleState.SHUTDOWN_FAILED
                self.shutdown_phase = ShutdownPhase.FAILED
                self.shutdown_failed_reported = True
                return
            self._kill_sent = True
            self.shutdown_phase = ShutdownPhase.FORCED


__all__ = [
    "FORCED_TERMINATION_TIMEOUT_SECONDS",
    "GRACEFUL_TERMINATION_TIMEOUT_SECONDS",
    "TOTAL_SHUTDOWN_BUDGET_SECONDS",
    "Clock",
    "LifecycleManager",
    "LifecycleState",
    "OperationResult",
    "ProcessAdapter",
    "ProcessHandle",
    "ProcessOwnershipUncertainError",
    "ProcessSpawnError",
    "ShutdownPhase",
    "build_launch_args",
]
