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

"""Periodic lifecycle observation driven by ``QTimer`` (STARTING/RUNNING).

The observer owns a single ``QTimer`` with a nominal 100 ms interval and
delegates each timeout to :meth:`LifecycleManager.observe`. It introduces no
deadline, retry policy, timeout, or new lifecycle state: all state transitions
remain owned by the lifecycle manager. Unknown observations therefore preserve
``STARTING``; only confirmed liveness resolves ``STARTING`` to ``RUNNING``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

#: Nominal observation interval in milliseconds (ratified for the periodic
#: observation controller only; not a shutdown/readiness deadline).
OBSERVE_INTERVAL_MS = 100


def _create_qtimer() -> Any:
    """Create a real ``QTimer`` with a lazy PySide6 import.

    The import stays lazy so non-GUI modules remain importable without Qt.
    """
    from PySide6.QtCore import QTimer

    return QTimer()


class LifecycleObserver:
    """Drive ``LifecycleManager.observe`` from the Qt event loop.

    The timer fires in the Qt event loop thread; no blocking waits, threads,
    or executors are used. Observation exceptions are swallowed so a single
    failed probe never stops periodic observation nor propagates out of the
    Qt slot.
    """

    def __init__(
        self,
        manager: Any,
        timer_factory: Optional[Callable[[], Any]] = None,
        interval_ms: int = OBSERVE_INTERVAL_MS,
    ) -> None:
        self._manager = manager
        self._interval_ms = interval_ms
        factory = timer_factory if timer_factory is not None else _create_qtimer
        self._timer = factory()
        self._timer.setInterval(self._interval_ms)
        self._connected = False
        self._connect_once()

    def _connect_once(self) -> None:
        if self._connected:
            return
        self._timer.timeout.connect(self._on_timeout)
        self._connected = True

    @property
    def interval_ms(self) -> int:
        return self._interval_ms

    @property
    def timer(self) -> Any:
        return self._timer

    def is_running(self) -> bool:
        return bool(self._timer.isActive())

    def start(self) -> None:
        """Start periodic observation (idempotent, no duplicate timers)."""
        self._connect_once()
        self._timer.setInterval(self._interval_ms)
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """Stop periodic observation (idempotent)."""
        self._timer.stop()

    def _on_timeout(self) -> None:
        try:
            self._manager.observe()
        except Exception:
            # Safe handling: a failing probe must not break the Qt slot
            # nor stop periodic observation.
            return


__all__ = ["OBSERVE_INTERVAL_MS", "LifecycleObserver"]
