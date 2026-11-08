# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import warnings
from enum import Enum
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot

from castlearq_desktop.core.managed_environment_installer import (
    _CancelToken,
    install_core_0_6_0,
    InstallResult,
    InstallStatus,
)


class InstallationState(str, Enum):
    IDLE = "idle"
    INSTALLING = "installing"
    CANCELLING = "cancelling"
    INSTALLED = "installed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class _InstallThread(QThread):
    """Run the blocking installer without calling the coordinator directly."""

    def __init__(
        self,
        root_override: Optional[Any],
        cancel_token: _CancelToken,
    ) -> None:
        super().__init__()
        self.root_override = root_override
        self.cancel_token = cancel_token
        self.result: Optional[InstallResult] = None
        self.error: Optional[str] = None

    def run(self) -> None:
        try:
            self.result = install_core_0_6_0(
                root_override=self.root_override,
                cancel_token=self.cancel_token,
            )
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)


class _ThreadRegistry(QObject):
    """Keep active QThread wrappers alive until Qt confirms full termination."""

    def __init__(self) -> None:
        super().__init__()
        self._threads: set[_InstallThread] = set()
        self._collector = QTimer(self)
        self._collector.setInterval(10)
        self._collector.timeout.connect(self._collect_finished_threads)

    def retain(self, thread: _InstallThread) -> None:
        self._threads.add(thread)
        thread.finished.connect(
            self._on_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )

    @Slot()
    def _on_thread_finished(self) -> None:
        # finished() can precede completion of thread-local cleanup. Poll with
        # wait(0), which is non-blocking, before releasing the last registry ref.
        self._collect_finished_threads()

    @Slot()
    def _collect_finished_threads(self) -> None:
        for thread in tuple(self._threads):
            if thread.isFinished() and thread.wait(0):
                self._threads.discard(thread)
        if self._threads:
            if not self._collector.isActive():
                self._collector.start()
        else:
            self._collector.stop()


_thread_registry = _ThreadRegistry()


class InstallationCoordinator(QObject):
    state_changed = Signal(object)
    finished = Signal(object)
    error = Signal(str)
    cancellation_requested = Signal()
    cancelled = Signal()

    def __init__(self, parent: Optional[QObject] = None, pool: Optional[Any] = None):
        super().__init__(parent)
        if pool is not None:
            warnings.warn(
                "InstallationCoordinator(pool=...) is deprecated and no longer "
                "selects the worker scheduler; omit pool. Installation now uses QThread.",
                FutureWarning,
                stacklevel=2,
            )
        self._state = InstallationState.IDLE
        self._result: Optional[InstallResult] = None
        self._thread: Optional[_InstallThread] = None
        self._root_override: Optional[Any] = None
        self._cancel_token: Optional[_CancelToken] = None
        self._invalidated = False
        self._completing = False
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.setSingleShot(True)
        self._cleanup_timer.setInterval(10)
        self._cleanup_timer.timeout.connect(self._poll_thread_completion)

    def state(self) -> InstallationState:
        return self._state

    def result(self) -> Optional[InstallResult]:
        return self._result

    def is_busy(self) -> bool:
        return self._state in (
            InstallationState.INSTALLING,
            InstallationState.CANCELLING,
        )

    def start(self, root_override: Optional[Any] = None) -> bool:
        if self.is_busy() or self._thread is not None or self._completing:
            return False

        self._root_override = root_override
        self._result = None
        self._invalidated = False
        cancel_token = _CancelToken()
        thread = _InstallThread(root_override, cancel_token)
        self._thread = thread
        self._cancel_token = cancel_token
        _thread_registry.retain(thread)
        thread.finished.connect(
            self._on_worker_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._set_state(InstallationState.INSTALLING)
        thread.start()
        return True

    def cancel(self) -> None:
        """Request cooperative cancellation; completion is reported later."""
        thread = self._thread
        if self._state != InstallationState.INSTALLING or thread is None:
            return

        # If Qt has already fully stopped the worker, completion wins the race
        # and is processed before considering a new cancellation request.
        if thread.wait(0):
            self._complete_thread(thread)
            return

        self._invalidated = True
        if self._cancel_token is not None:
            self._cancel_token.request_cancellation()
        self._set_state(InstallationState.CANCELLING)
        self.cancellation_requested.emit()

    def _set_state(self, state: InstallationState) -> None:
        self._state = state
        self.state_changed.emit(state)

    @Slot()
    def _on_worker_finished(self) -> None:
        thread = self.sender()
        if not isinstance(thread, _InstallThread) or thread is not self._thread:
            return
        self._complete_thread(thread)

    @Slot()
    def _poll_thread_completion(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if thread.wait(0):
            self._complete_thread(thread)
        else:
            self._cleanup_timer.start()

    def _complete_thread(self, thread: _InstallThread) -> None:
        if thread is not self._thread or not thread.wait(0):
            return

        # wait(0) confirms all thread effects have completed; only now release
        # the coordinator's own reference and allow a subsequent start().
        self._completing = True
        try:
            self._thread = None
            self._cancel_token = None
            self._root_override = None
            self._cleanup_timer.stop()

            if self._invalidated:
                self._invalidated = False
                self._result = None
                self._set_state(InstallationState.CANCELLED)
                self.cancelled.emit()
                return

            if thread.error is not None:
                self._result = None
                self._set_state(InstallationState.FAILED)
                self.error.emit(thread.error)
                self.finished.emit(None)
                return

            result = thread.result
            self._result = result
            if result is not None and result.status in (
                InstallStatus.SUCCESS,
                InstallStatus.ALREADY_INSTALLED,
            ):
                self._set_state(InstallationState.INSTALLED)
            elif result is not None and result.status == InstallStatus.CANCELLED:
                self._set_state(InstallationState.CANCELLED)
                self.cancelled.emit()
            else:
                self._set_state(InstallationState.FAILED)
                if result is not None and result.error:
                    self.error.emit(result.error)
            self.finished.emit(result)
        finally:
            self._completing = False
