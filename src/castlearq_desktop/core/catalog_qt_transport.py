# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Qt async transport for GET /v1/models.

Worker runs on QThreadPool; delivery is a queued Qt signal on the owning
thread. Mirrors VersionProbeBridge / VersionProbeRunnable patterns.
"""

from __future__ import annotations

import weakref
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from castlearq_desktop.core.catalog_client import CatalogResult, fetch_catalog


class CatalogBridge(QObject):
    """Qt-side conduit delivering one catalog result to the owning thread."""

    resolved = Signal(object)

    def __init__(
        self,
        on_resolved: Callable[[Any], None],
        parent: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self._on_resolved = on_resolved
        self.resolved.connect(self._dispatch)

    def deliver(self, payload: Any) -> None:
        if self._on_resolved is None:
            return
        self.resolved.emit(payload)

    def _dispatch(self, payload: Any) -> None:
        callback = self._on_resolved
        if callback is None:
            return
        try:
            generation, result = payload
            callback(generation, result)
        except Exception:  # noqa: BLE001
            pass


class CatalogRunnable(QRunnable):
    """Worker that fetches the catalog off the GUI thread."""

    def __init__(
        self,
        bridge_ref: "weakref.ReferenceType[Any]",
        generation: int,
        fetch: Optional[Callable[..., bytes]] = None,
    ) -> None:
        super().__init__()
        self._bridge_ref = bridge_ref
        self._generation = generation
        self._fetch = fetch
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = fetch_catalog(fetch=self._fetch)
        except Exception:  # noqa: BLE001
            result = CatalogResult(error="No se puede conectar con CastleArq Core")
        bridge = self._bridge_ref()
        if bridge is None:
            return
        try:
            bridge.deliver((self._generation, result))
        except Exception:  # noqa: BLE001
            pass


def start_catalog_fetch(
    on_result: Callable[[int, CatalogResult], None],
    generation: int,
    fetch: Optional[Callable[..., bytes]] = None,
    pool: Optional[QThreadPool] = None,
) -> CatalogBridge:
    """Queue one catalog fetch; returns the bridge for lifetime management."""
    bridge = CatalogBridge(on_result)
    runnable = CatalogRunnable(weakref.ref(bridge), generation, fetch)
    (pool or QThreadPool.globalInstance()).start(runnable)
    return bridge


__all__ = ["CatalogBridge", "CatalogRunnable", "start_catalog_fetch"]