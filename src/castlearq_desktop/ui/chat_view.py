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

"""Chat section: a minimal chat over Core's persistent chat sessions.

The view is a **thin** presentation layer: it never keeps the conversation
itself. Every message shown here was produced by Core for a real session, and
the session lives in Core, so the history is the real one.

The view refuses to send while a turn is generating (Core rejects a concurrent
turn for the same session anyway) and it drops any answer that arrives after
the session was closed or replaced.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from castlearq_desktop.core.chat_service import ChatService
from castlearq_desktop.core.download_service import LocalArtifact

__all__ = ["ChatView"]

_TITLE = "Chat"
_IDLE_TEXT = "Sin sesión de chat abierta."
_OPENING_TEXT = "Abriendo la sesión de chat en el Core…"
_NO_ARTIFACT_TEXT = "Selecciona un artefacto local para iniciar el chat."
_START_TEXT = "Iniciar chat"
_SEND_TEXT = "Enviar"
_CLOSE_TEXT = "Cerrar chat"


class ChatView(QWidget):
    """Section that starts a Core chat session and shows its turns."""

    session_closed = Signal()

    def __init__(
        self,
        service: ChatService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._artifact: Optional[LocalArtifact] = None
        self._session_id: Optional[str] = None
        self._awaiting_turn: bool = False
        self._last_prompt: Optional[str] = None

        box = QGroupBox(_TITLE, self)
        layout = QVBoxLayout(box)

        self._status = QLabel(_NO_ARTIFACT_TEXT)
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._status)

        self._history = QTextBrowser(box)
        self._history.setReadOnly(True)
        layout.addWidget(self._history)

        self._prompt = QPlainTextEdit(box)
        self._prompt.setPlaceholderText("Escribe tu mensaje…")
        self._prompt.setEnabled(False)
        layout.addWidget(self._prompt)

        buttons = QHBoxLayout()
        self._start_button = QPushButton(_START_TEXT, box)
        self._start_button.clicked.connect(self._on_start)
        buttons.addWidget(self._start_button)
        self._send_button = QPushButton(_SEND_TEXT, box)
        self._send_button.clicked.connect(self._on_send)
        self._send_button.setEnabled(False)
        buttons.addWidget(self._send_button)
        self._close_button = QPushButton(_CLOSE_TEXT, box)
        self._close_button.clicked.connect(self._on_close)
        self._close_button.setEnabled(False)
        buttons.addWidget(self._close_button)
        layout.addLayout(buttons)

        self._service.session_opened.connect(self._on_session_opened)
        self._service.turn_completed.connect(self._on_turn_completed)
        self._service.failed.connect(self._on_failed)
        self._service.busy_changed.connect(self._on_busy_changed)

        self._update_controls()

    # -- external state -----------------------------------------------------

    @property
    def has_session(self) -> bool:
        """Whether a Core chat session is currently open.

        Delegates to the service: the session lives in Core and the service
        is its single source of truth (no session state is duplicated here).
        """
        return bool(getattr(self._service, "has_session", False))

    def set_artifact(self, artifact: Optional[LocalArtifact]) -> None:
        """Select (or clear) the artifact the chat will be opened for."""
        self._artifact = artifact
        self._awaiting_turn = False
        self._last_prompt = None
        if self._session_id is not None:
            # An open session belongs to the previous artifact: it is closed
            # on Core so no runtime outlives the change.
            self.close_session()
        if artifact is None:
            self._status.setText(_NO_ARTIFACT_TEXT)
        else:
            self._status.setText(
                f"Artefacto para el chat: {artifact.filename} ({artifact.model_id})"
            )
        self._update_controls()

    # -- actions ------------------------------------------------------------

    def _on_start(self) -> None:
        """Open a real chat session on Core for the selected artifact."""
        artifact = self._artifact
        if artifact is None:
            self._status.setText(_NO_ARTIFACT_TEXT)
            return
        accepted = self._service.open_session(
            artifact.model_id,
            filename=artifact.filename,
            quantization=artifact.quantization,
        )
        if not accepted:
            # Refused (busy, duplicate open, missing model): the visible
            # conversation and the status stay untouched; only the
            # controls are refreshed from the real state of the service.
            self._update_controls()
            return
        self._history.clear()
        self._status.setText(_OPENING_TEXT)
        self._update_controls()

    def _on_send(self) -> None:
        """Send the prompt of the current session."""
        prompt = self._prompt.toPlainText().strip()
        if not prompt:
            return
        accepted = self._service.send_turn(prompt)
        if not accepted:
            # Rejected (busy, no session, empty): nothing was sent, so the
            # conversation stays untouched and the input is preserved.
            self._status.setText("El turno no fue aceptado.")
            self._prompt.setPlainText(prompt)
            self._update_controls()
            return
        self._prompt.clear()
        self._append(f"Tú: {prompt}")
        self._awaiting_turn = True
        self._last_prompt = prompt

    def _on_close(self) -> None:
        """Close the session on Core and drop the visible history."""
        self.close_session()

    def close_session(self) -> None:
        """Close the Core session (if any) and reset the view."""
        self._service.close_session()
        self._session_id = None
        self._awaiting_turn = False
        self._last_prompt = None
        self._history.clear()
        self._prompt.clear()
        self._prompt.setEnabled(False)
        artifact = self._artifact
        if artifact is None:
            self._status.setText(_NO_ARTIFACT_TEXT)
        else:
            self._status.setText(
                f"Artefacto para el chat: {artifact.filename} ({artifact.model_id})"
            )
        self._update_controls()
        self.session_closed.emit()

    def invalidate(self) -> None:
        """Drop pending results (window close / Core shutdown)."""
        self._service.invalidate()
        self._session_id = None
        self._awaiting_turn = False
        self._last_prompt = None
        self._history.clear()
        self._prompt.setEnabled(False)
        self._update_controls()

    # -- service callbacks --------------------------------------------------

    def _on_session_opened(self, session_id: str, model_id: str) -> None:
        self._session_id = session_id
        self._status.setText(f"Sesión de chat abierta en el Core ({model_id}).")
        self._prompt.setEnabled(True)
        self._update_controls()

    def _on_turn_completed(self, response: str, turn_count: int) -> None:
        self._awaiting_turn = False
        self._last_prompt = None
        if response.strip() == "":
            self._append(f"Core ({turn_count}): (sin contenido)")
            self._status.setText(
                f"Respuesta recibida sin contenido ({turn_count} turno(s))."
            )
        else:
            self._append(f"Core ({turn_count}): {response}")
            self._status.setText(f"Respuesta recibida ({turn_count} turno(s)).")

    def _on_failed(self, message: str) -> None:
        if self._awaiting_turn:
            # The accepted turn did not complete: record the failure in the
            # conversation without touching earlier messages.
            self._awaiting_turn = False
            self._append(f"Error: {message}")
            if self._prompt.toPlainText().strip() == "" and self._last_prompt is not None:
                self._prompt.setPlainText(self._last_prompt)
            self._last_prompt = None
        self._status.setText(message)
        self._update_controls()

    def _on_busy_changed(self, busy: bool) -> None:
        self._update_controls(busy=busy)

    # -- internals ----------------------------------------------------------

    def _append(self, text: str) -> None:
        self._history.append(text)

    def _update_controls(self, busy: Optional[bool] = None) -> None:
        """Refresh the enabled state of the chat controls."""
        if busy is None:
            busy = self._service.is_busy
        has_session = self._session_id is not None
        self._start_button.setEnabled(
            not busy and not has_session and self._artifact is not None
        )
        self._send_button.setEnabled(not busy and has_session)
        self._close_button.setEnabled(not busy and has_session)
        self._prompt.setEnabled(not busy and has_session)
