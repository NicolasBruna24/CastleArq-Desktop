# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Single-flight async driver for one manual model catalog check."""

from __future__ import annotations

import weakref
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThreadPool, Signal

from castlearq_desktop.core.catalog_client import CatalogResult
from castlearq_desktop.core.catalog_qt_transport import start_catalog_fetch


class CatalogController(QObject):
    """Drives one catalog query at a time, with generation-based invalidation."""

    check_started = Signal()
    already_running = Signal()
    check_finished = Signal(object)  # CatalogResult
    check_failed = Signal(str)  # user message

    def __init__(
        self,
        is_ready: Optional[Callable[[], bool]] = None,
        fetch: Optional[Callable[..., bytes]] = None,
        thread_pool: Optional[QThreadPool] = None,
        parent: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self._is_ready = is_ready
        self._fetch = fetch
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._generation = 0
        self._active_generation: Optional[int] = None
        self._active_bridge: Optional[Any] = None

    @property
    def is_checking(self) -> bool:
        return self._active_generation is not None

    def check(self) -> bool:
        """Start one query; second trigger while busy emits already_running."""
        if self._active_generation is not None:
            self.already_running.emit()
            return False
        if self._is_ready is not None and not self._is_ready():
            self.check_failed.emit("Core no disponible")
            return False
        self._generation += 1
        self._active_generation = self._generation
        self._active_bridge = start_catalog_fetch(
            self._on_result,
            self._generation,
            fetch=self._fetch,
            pool=self._pool,
        )
        self.check_started.emit()
        return True

    def invalidate(self) -> None:
        """Retire active generation; late results are dropped."""
        self._active_generation = None
        self._active_bridge = None

    def _on_result(self, generation: int, result: CatalogResult) -> None:
        if generation != self._active_generation:
            return
        self._active_generation = None
        self._active_bridge = None
        if result.error is not None:
            self.check_failed.emit(result.error)
        else:
            self.check_finished.emit(result)


__all__ = ["CatalogController"]