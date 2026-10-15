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

"""Asynchronous installed-version probe for DEC-A readiness.

The production readiness path needs the *really installed* ``castlearq``
version to evaluate DEC-A admission. Resolving it means running the
explicitly selected interpreter through
:func:`~castlearq_desktop.core.version_admission.query_installed_core_version`,
which is a bounded blocking subprocess (5 s hard timeout). Doing that on the
Qt GUI thread would freeze the window, so this module moves the work onto a
worker and hands the outcome back to the owning thread through a queued Qt
signal.

The structure deliberately mirrors the established pattern in
``update_check_controller.py``:

* :class:`VersionProbeRunnable` performs the query and touches no Qt object,
  no coordinator cache and no readiness state;
* :class:`VersionProbeBridge` is the only Qt object involved. It is created
  on — and therefore owned by — the thread that runs the event loop, so
  emitting its signal from the worker is delivered as a *queued* call on that
  same thread. The worker never manipulates Qt objects directly;
* the worker holds only a **weak** reference to the bridge, so a bridge that
  no longer exists can never be signalled;
* every result carries the startup-attempt *generation* that produced it, so
  a late result from a superseded attempt is discarded instead of applied.

Invalidation is logical only: it drops stale results, it does **not** cancel a
running subprocess and it never blocks the caller.

This module owns no policy. Admission, allowlisting and readiness remain in
``version_admission`` and ``readiness_qt_transport``; a probe that cannot
resolve a version reports ``None``, which the admission policy treats as
fail-closed.
"""

from __future__ import annotations

import weakref
from typing import Any, Callable, Optional, Tuple

from PySide6.QtCore import QObject, QRunnable, Signal

from castlearq_desktop.core.version_admission import query_installed_core_version


class VersionProbeBridge(QObject):
    """Qt-side conduit delivering one probe result to the owning thread.

    The bridge lives in the thread that constructs it (the GUI thread in
    production). :meth:`deliver` may be called from any thread: the emission
    is queued and :meth:`_dispatch` always runs on the bridge's own thread,
    which is what makes it safe for the consumer to touch coordinator state.
    """

    #: Emitted with ``(generation, version)``. ``version`` is ``None`` when
    #: the probe could not resolve one, which stays fail-closed.
    resolved = Signal(object)

    def __init__(
        self,
        on_resolved: Callable[[Any], None],
        parent: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self._on_resolved = on_resolved
        # Self-connection keeps every delivery on the owning thread.
        self.resolved.connect(self._dispatch)

    def deliver(self, payload: Tuple[int, Optional[str]]) -> None:
        """Hand *payload* to the owning thread; callable from a worker."""
        if self._on_resolved is None:
            return
        self.resolved.emit(payload)

    def _dispatch(self, payload: Any) -> None:
        """Run the consumer callback on the bridge's owning thread."""
        callback = self._on_resolved
        if callback is None:
            return
        try:
            callback(payload)
        except Exception:  # noqa: BLE001 - a stale consumer must stay inert
            pass


class VersionProbeRunnable(QRunnable):
    """Worker that resolves the installed Core version off the GUI thread.

    ``run`` performs only the bounded probe and then offers the outcome to
    the bridge. It writes no coordinator state, resumes no readiness and
    touches no Qt object beyond that single queued emission. Any failure
    resolves to ``None`` so a broken probe can never look like an admitted
    version.
    """

    def __init__(
        self,
        bridge_ref: "weakref.ReferenceType[Any]",
        generation: int,
        python_path: str,
        executor: Optional[Callable[..., Any]] = None,
    ) -> None:
        super().__init__()
        self._bridge_ref = bridge_ref
        self._generation = generation
        self._python_path = python_path
        self._executor = executor
        self.setAutoDelete(True)

    def run(self) -> None:
        """Execute the bounded probe and offer the result to the bridge.

        The existing implementation is reused unchanged, including its 5 s
        hard timeout, its single attempt and its isolated-mode command. No
        retry and no fallback interpreter is introduced here.
        """
        try:
            version = query_installed_core_version(
                self._python_path, execute=self._executor
            )
        except Exception:  # noqa: BLE001 - fail closed, never admit
            version = None
        bridge = self._bridge_ref()
        if bridge is None:
            # The bridge is gone: nothing may be signalled and no state
            # may be reached. The outcome is simply abandoned.
            return
        try:
            bridge.deliver((self._generation, version))
        except Exception:  # noqa: BLE001 - a closed bridge must stay inert
            pass


__all__ = ["VersionProbeBridge", "VersionProbeRunnable"]
