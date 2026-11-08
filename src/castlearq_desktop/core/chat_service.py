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

"""Qt service driving Core's persistent chat from the Desktop UI.

The conversation lives in Core, not in the UI: the service opens a **real**
session on Core and sends turns through it, so the history the user sees is
the history Core actually holds.

Every HTTP call runs on a ``QThreadPool`` worker, so the GUI thread is never
blocked while a model generates. Two invariants are enforced here:

* **Single flight.** Only one chat operation runs at a time; a second
  request while one is in flight is refused instead of racing.
* **No stale results.** Each operation carries a generation number. When the
  view closes, the model changes, or a new operation supersedes the previous
  one, the generation advances and late deliveries are dropped.

Closing the service deletes the session on Core, so no runtime outlives the
Desktop window.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from castlearq_desktop.core.chat_client import (
    ChatClientError,
    ChatSessionInfo,
    ChatTurnResult,
    CoreChatClient,
)

__all__ = ["ChatService"]

_logger = logging.getLogger(__name__)


class _OpenSessionGuard:
    """Coordinate invalidation with a worker that may still open a session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._invalidated = False
        self._info: Optional[ChatSessionInfo] = None
        self._cleanup_claimed = False

    def complete(self, info: ChatSessionInfo) -> tuple[bool, bool]:
        """Return (may_publish, must_clean_up_here) for an open result."""
        with self._lock:
            self._info = info
            if not self._invalidated:
                return True, False
            if self._cleanup_claimed:
                return False, False
            self._cleanup_claimed = True
            return False, True

    def invalidate(self) -> Optional[ChatSessionInfo]:
        """Retire this open and claim cleanup if its result already arrived."""
        with self._lock:
            self._invalidated = True
            if self._info is None or self._cleanup_claimed:
                return None
            self._cleanup_claimed = True
            return self._info


def _delete_session_safely(client: CoreChatClient, session_id: str) -> None:
    try:
        client.delete_session(session_id)
    except Exception:  # noqa: BLE001 - cleanup must not revive stale state
        _logger.warning("Could not clean up an invalidated Chat session", exc_info=True)


class _SessionCleanupRunnable(QRunnable):
    """Delete a session without involving the current Chat UI generation."""

    def __init__(self, client: CoreChatClient, session_id: str) -> None:
        super().__init__()
        self._client = client
        self._session_id = session_id

    def run(self) -> None:
        _delete_session_safely(self._client, self._session_id)


class _ChatBridge(QObject):
    """Qt-side conduit delivering a worker result to the owning thread.

    The bridge carries the generation of the operation it serves, so a late
    failure of a superseded operation can never be mistaken for the failure
    of the operation running now.
    """

    resolved = Signal(object)
    failed = Signal(int, str)  # generation, user message

    def __init__(self, generation: int, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._generation = generation

    def deliver(self, payload: Any) -> None:
        self.resolved.emit(payload)

    def report_failure(self, message: str) -> None:
        self.failed.emit(self._generation, message)


class _ChatRunnable(QRunnable):
    """Worker performing one chat operation against Core."""

    def __init__(
        self,
        bridge: _ChatBridge,
        generation: int,
        operation: str,
        client: CoreChatClient,
        *,
        model_id: Optional[str] = None,
        filename: Optional[str] = None,
        quantization: Optional[str] = None,
        device: Optional[str] = None,

        session_id: Optional[str] = None,
        prompt: Optional[str] = None,
        open_guard: Optional[_OpenSessionGuard] = None,
    ) -> None:
        super().__init__()
        self._bridge = bridge
        self._generation = generation
        self._operation = operation
        self._client = client
        self._model_id = model_id
        self._filename = filename
        self._quantization = quantization
        self._device = device

        self._session_id = session_id
        self._prompt = prompt
        self._open_guard = open_guard
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            if self._operation == "open":
                info = self._client.create_session(
                    self._model_id or "",
                    filename=self._filename,
                    quantization=self._quantization,
                    device=self._device,

                )
                if self._open_guard is not None:
                    may_publish, clean_up_here = self._open_guard.complete(info)
                    if not may_publish:
                        if clean_up_here:
                            _delete_session_safely(self._client, info.session_id)
                        return
                payload = (self._generation, "opened", info, None)
            elif self._operation == "turn":
                result = self._client.send_turn(self._session_id or "", self._prompt or "")
                payload = (self._generation, "turn", result, None)
            elif self._operation == "close":
                self._client.delete_session(self._session_id or "")
                payload = (self._generation, "closed", None, None)
            else:  # pragma: no cover - defensive
                raise ChatClientError(
                    "Operación de chat desconocida.", reason="invalid_request"
                )
        except ChatClientError as exc:
            self._bridge.report_failure(exc.user_message)
            return
        except Exception:  # noqa: BLE001 - never leak internals
            self._bridge.report_failure(
                "No se pudo completar la operación de chat con CastleArq Core."
            )
            return
        self._bridge.deliver(payload)


class ChatService(QObject):
    """Asynchronous facade over :class:`CoreChatClient`."""

    session_opened = Signal(str, str)  # session_id, model_id
    turn_completed = Signal(str, int)  # response, turn_count
    open_session_refused = Signal(str)  # reason opening was refused locally
    failed = Signal(str)  # user message
    busy_changed = Signal(bool)
    closed = Signal()

    def __init__(
        self,
        *,
        client: Optional[CoreChatClient] = None,
        thread_pool_factory: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._client = client or CoreChatClient()
        self._thread_pool_factory = (
            thread_pool_factory
            if thread_pool_factory is not None
            else QThreadPool.globalInstance
        )
        self._generation = 0
        self._active_generation = 0
        self._session_id: Optional[str] = None
        self._model_id: Optional[str] = None
        self._bridge: Optional[_ChatBridge] = None
        self._shutting_down = False
        self._active_operation: Optional[str] = None
        self._pending_open_guard: Optional[_OpenSessionGuard] = None

    # -- public state ------------------------------------------------------

    @property
    def session_id(self) -> Optional[str]:
        """The Core session currently open, if any."""
        return self._session_id

    @property
    def model_id(self) -> Optional[str]:
        """The model the open session belongs to."""
        return self._model_id

    @property
    def has_session(self) -> bool:
        """Whether a session is open on Core."""
        return self._session_id is not None

    @property
    def is_busy(self) -> bool:
        """Whether a chat operation is currently running."""
        return self._active_generation > 0

    # -- operations --------------------------------------------------------

    def open_session(
        self,
        model_id: str,
        *,
        filename: Optional[str] = None,
        quantization: Optional[str] = None,
        device: Optional[str] = None,

    ) -> bool:
        """Open a chat session on Core. Returns False when refused."""
        if self._shutting_down:
            self.open_session_refused.emit(
                "Chat se está cerrando y no puede abrir otra sesión."
            )
            return False
        if self.is_busy:
            messages = {
                "open": "Ya se está abriendo una sesión de chat; espera a que termine.",
                "turn": "Hay un turno de chat en curso; espera a que termine.",
                "close": "La sesión de chat se está cerrando; espera a que termine.",
            }
            self.open_session_refused.emit(
                messages.get(
                    self._active_operation,
                    "Chat está ocupado; espera a que termine la operación actual.",
                )
            )
            return False
        if self.has_session:
            self.open_session_refused.emit(
                "Ya hay una sesión activa. Puedes continuarla o cerrarla antes de iniciar otra."
            )
            return False
        if not isinstance(model_id, str) or not model_id.strip():
            self.failed.emit("No hay un modelo seleccionado para iniciar el chat.")
            return False
        self._generation += 1
        self._active_generation = self._generation
        open_guard = _OpenSessionGuard()
        self._pending_open_guard = open_guard
        self._start(
            "open",
            model_id=model_id,
            filename=filename,
            quantization=quantization,
            device=device,
            open_guard=open_guard,

        )
        return True

    def send_turn(self, prompt: str) -> bool:
        """Send one prompt on the open session. Returns False when refused."""
        if self._shutting_down or self.is_busy:
            return False
        if self._session_id is None:
            self.failed.emit("No hay una sesión de chat abierta.")
            return False
        if not isinstance(prompt, str) or not prompt.strip():
            self.failed.emit("Escribe un mensaje antes de enviarlo.")
            return False
        self._generation += 1
        self._active_generation = self._generation
        self._start("turn", session_id=self._session_id, prompt=prompt)
        return True

    def close_session(self) -> bool:
        """Close the session on Core, discarding any in-flight operation."""
        session_id = self._session_id
        if not session_id:
            if self._pending_open_guard is not None:
                self.invalidate()
                return False
            # No session to close and no operation started on its behalf:
            # the generation counter must stay untouched. Bumping
            # _active_generation here would mark a never-started operation
            # as active, leaving the service busy forever and silently
            # refusing every later open/turn. A real operation already in
            # flight (e.g. an open still running) is deliberately left
            # alone: its own generation keeps protecting its result.
            return False
        self._session_id = None
        self._model_id = None
        if self._shutting_down:
            # close() owns the Core-side deletion during shutdown; only the
            # local reference is dropped here.
            return False
        # Any operation still in flight is now stale.
        self._generation += 1
        self._active_generation = self._generation
        self._bridge = None
        self._start("close", session_id=session_id, keep_busy=False)
        return True

    def invalidate(self) -> None:
        """Invalidate pending work and asynchronously close all known sessions."""
        if (
            not self.is_busy
            and self._session_id is None
            and self._pending_open_guard is None
            and self._bridge is None
            and self._active_operation is None
        ):
            return
        was_busy = self.is_busy
        session_id = self._session_id
        self._session_id = None
        self._model_id = None
        self._generation += 1
        self._active_generation = 0
        self._bridge = None
        self._active_operation = None
        open_guard = self._pending_open_guard
        self._pending_open_guard = None
        if open_guard is not None:
            info = open_guard.invalidate()
            if info is not None:
                self._schedule_session_cleanup(info.session_id)
        if session_id:
            self._schedule_session_cleanup(session_id)
        if was_busy:
            self.busy_changed.emit(False)

    def close(self) -> None:
        """Stop accepting operations and release the session on Core."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self.invalidate()
        self.closed.emit()

    def _schedule_session_cleanup(self, session_id: str) -> None:
        runnable = _SessionCleanupRunnable(self._client, session_id)
        try:
            self._thread_pool_factory().start(runnable)
        except Exception:  # noqa: BLE001 - never break lifecycle invalidation
            _logger.warning(
                "Could not schedule cleanup for an invalidated Chat session",
                exc_info=True,
            )

    # -- internals ---------------------------------------------------------

    def _start(
        self,
        operation: str,
        *,
        keep_busy: bool = True,
        open_guard: Optional[_OpenSessionGuard] = None,
        **kwargs: Any,
    ) -> None:
        self._active_operation = operation
        bridge = _ChatBridge(self._generation, self)
        bridge.resolved.connect(self._on_resolved)
        bridge.failed.connect(self._on_failed)
        self._bridge = bridge
        runnable = _ChatRunnable(
            bridge,
            self._generation,
            operation,
            self._client,
            open_guard=open_guard,
            **kwargs,
        )
        self._thread_pool_factory().start(runnable)
        if keep_busy:
            self.busy_changed.emit(True)

    def _on_resolved(self, payload: Any) -> None:
        try:
            generation, kind, data, _ = payload
        except (TypeError, ValueError):
            return
        if generation != self._active_generation:
            return  # stale result after invalidation
        if kind == "opened":
            self._pending_open_guard = None
        self._active_generation = 0
        self._bridge = None
        self._active_operation = None
        self.busy_changed.emit(False)
        if kind == "opened":
            info: ChatSessionInfo = data
            self._session_id = info.session_id
            self._model_id = info.model_id
            self.session_opened.emit(info.session_id, info.model_id)
        elif kind == "turn":
            result: ChatTurnResult = data
            self.turn_completed.emit(result.response, result.turn_count)
        elif kind == "closed":
            self.closed.emit()

    def _on_failed(self, generation: int, message: str) -> None:
        if generation != self._active_generation:
            # The failure belongs to a superseded operation: dropping it
            # keeps a late error from being reported as the current one.
            return
        if self._active_operation == "open":
            self._pending_open_guard = None
        self._active_generation = 0
        self._bridge = None
        self._active_operation = None
        self.busy_changed.emit(False)
        self.failed.emit(message)
