# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

"""Qt async wrapper for Core Update Discovery v1 (Stage 3).

Runs sync service off GUI thread via global QThreadPool. Single flight,
generation ids drop stale results, invalidate() is logical only (never
blocks close), delivery via queued Qt signals. Touches no lifecycle,
contract, ErrorCode, or allowlist.
"""

from __future__ import annotations

import weakref
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from castlearq_desktop.core.update_discovery import (
    UpdateDiscoveryResult,
    discover_core_update,
)
from castlearq_desktop.core.version_admission import SUPPORTED_CORE_VERSIONS


class _DiscoveryRunnable(QRunnable):
    """Worker running one discovery; reports via weak controller ref."""

    def __init__(self, ref, generation, python_path, admitted, discover):
        super().__init__()
        self._ref = ref
        self._generation = generation
        self._python_path = python_path
        self._admitted = admitted
        self._discover = discover
        self.setAutoDelete(True)

    def run(self):
        try:
            result = self._discover(
                python_path=self._python_path,
                admitted_versions=self._admitted,
            )
        except Exception as exc:  # noqa: BLE001 - safe transport only
            ctl = self._ref()
            if ctl is not None:
                ctl._deliver_failure(self._generation, exc)
            return
        ctl = self._ref()
        if ctl is not None:
            ctl._deliver_result(self._generation, result)


class UpdateCheckController(QObject):
    """Single-flight async driver for one manual update check."""

    check_started = Signal()
    already_running = Signal()
    check_finished = Signal(object)
    check_failed = Signal(str)

    def __init__(self, interpreter_settings=None, discover=discover_core_update,
                 thread_pool=None, parent=None):
        super().__init__(parent)
        self._settings = interpreter_settings
        self._discover = discover
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._generation = 0
        self._active_generation = None

    @property
    def is_checking(self):
        return self._active_generation is not None

    def check(self):
        """Start one check; second trigger while busy emits already_running."""
        if self._active_generation is not None:
            self.already_running.emit()
            return False
        python_path = None
        if self._settings is not None:
            try:
                python_path = self._settings.load()
            except Exception:  # noqa: BLE001 - reads as unknown
                python_path = None
        self._generation += 1
        self._active_generation = self._generation
        runnable = _DiscoveryRunnable(
            weakref.ref(self), self._generation, python_path,
            frozenset(SUPPORTED_CORE_VERSIONS), self._discover,
        )
        self._pool.start(runnable)
        self.check_started.emit()
        return True

    def invalidate(self):
        """Retire active generation; late worker outcome is dropped.

        Logical only: a blocked network read is NOT interrupted; close never
        waits for the worker.
        """
        self._active_generation = None

    def _deliver_result(self, generation, result):
        if generation != self._active_generation:
            return
        self._active_generation = None
        self.check_finished.emit(result)

    def _deliver_failure(self, generation, exc):
        if generation != self._active_generation:
            return
        self._active_generation = None
        self.check_failed.emit(
            "The update check could not be completed. Please try again.")


__all__ = ["UpdateCheckController"]
