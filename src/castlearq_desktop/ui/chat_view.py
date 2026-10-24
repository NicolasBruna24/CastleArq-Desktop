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
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from castlearq_desktop.core.chat_service import ChatService
from castlearq_desktop.storage.conversation_store import ConversationStore, ConversationStoreError
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
    change_model_requested = Signal()

    def __init__(
        self,
        service: ChatService,
        store: Optional[ConversationStore] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._store = store
        self._artifact: Optional[LocalArtifact] = None
        self._session_id: Optional[str] = None
        self._conversation_id: Optional[str] = None
        self._messages: list[dict] = []
        self._history_mode: bool = False
        self._saved_history: str = ""
        self._saved_status: str = ""
        self._pending_summary: Optional[str] = None
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
        self._history_button = QPushButton("Historiales", box)
        self._history_button.clicked.connect(self._on_history)
        buttons.addWidget(self._history_button)
        self._back_button = QPushButton("Volver al chat", box)
        self._back_button.setEnabled(False)
        self._back_button.clicked.connect(self._on_back_from_history)
        buttons.addWidget(self._back_button)
        self._continue_button = QPushButton("Crear continuación", box)
        self._continue_button.setEnabled(False)
        self._continue_button.clicked.connect(self._on_create_continuation)
        buttons.addWidget(self._continue_button)
        self._send_button = QPushButton(_SEND_TEXT, box)
        self._send_button.clicked.connect(self._on_send)
        self._send_button.setEnabled(False)
        buttons.addWidget(self._send_button)
        self._close_button = QPushButton(_CLOSE_TEXT, box)
        self._close_button.clicked.connect(self._on_close)
        self._close_button.setEnabled(False)
        buttons.addWidget(self._close_button)

        self._change_model_button = QPushButton("Cambiar modelo", box)
        self._change_model_button.clicked.connect(
            self.change_model_requested.emit
        )
        buttons.addWidget(self._change_model_button)
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
        # Same artifact: do not close/reopen session unnecessarily.
        if artifact is not None and self._artifact is not None:
            if (
                self._artifact.model_id == artifact.model_id
                and self._artifact.filename == artifact.filename
                and self._artifact.quantization == artifact.quantization
            ):
                return
        if artifact is None and self._artifact is None:
            return
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
        if self._pending_summary:
            composed = (
                "Contexto proporcionado por el usuario:\n"
                f"{self._pending_summary}\n\n"
                f"Mensaje actual:\n{prompt}"
            )
            self._pending_summary = None
        else:
            composed = prompt
        accepted = self._service.send_turn(composed)
        if not accepted:
            # Rejected (busy, no session, empty): nothing was sent, so the
            # conversation stays untouched and the input is preserved.
            self._status.setText("El turno no fue aceptado.")
            self._prompt.setPlainText(prompt)
            self._update_controls()
            return
        self._prompt.clear()
        self._messages.append({"role": "user", "content": prompt})
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

    def _delete_history(self, cid: str):
        """Delete a saved conversation if it is not the active one.

        Returns a tuple (status, detail) where status is one of:
        'deleted', 'not_found', 'active', 'error'.
        """
        if cid == self._conversation_id:
            return ('active', None)
        if self._store is None:
            return ('error', 'store not configured')
        try:
            ok = self._store.delete_conversation(cid)
        except ConversationStoreError as exc:
            return ('error', str(exc))
        if ok:
            return ('deleted', None)
        return ('not_found', None)

    def _on_history(self) -> None:
        if self._store is None:
            return
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QListWidgetItem
        dlg = QDialog(self)
        dlg.setWindowTitle("Historiales guardados")
        layout = QVBoxLayout(dlg)
        lst = QListWidget(dlg)
        layout.addWidget(lst)
        for c in self._store.list_conversations():
            label = f"{c.get('model_id') or '?'} | {c.get('artifact_filename') or '?'} | {c.get('message_count',0)} msgs"
            item = QListWidgetItem(label)
            item.setData(32, c["id"])
            lst.addItem(item)
        from PySide6.QtWidgets import QPushButton, QMessageBox, QInputDialog
        del_btn = QPushButton("Eliminar", dlg)
        rename_btn = QPushButton("Renombrar", dlg)
        lst_layout = lst.parent().layout()
        lst_layout.addWidget(rename_btn)
        lst_layout.addWidget(del_btn)
        def _update_del():
            it = lst.currentItem()
            if it is None:
                del_btn.setEnabled(False); return
            cid = it.data(32)
            del_btn.setEnabled(cid != self._conversation_id)
        lst.currentItemChanged.connect(lambda *_: _update_del())
        _update_del()
        def _do_delete():
            it = lst.currentItem()
            if it is None: return
            cid = it.data(32)
            if cid == self._conversation_id:
                return
            meta = None
            for c in self._store.list_conversations():
                if c["id"] == cid:
                    meta = c; break
            desc = f"{meta.get('model_id') if meta else '?'} | {meta.get('artifact_filename') if meta else '?'}"
            if QMessageBox.question(dlg, "Confirmar", f"Eliminar {desc}?") != QMessageBox.StandardButton.Yes:
                return
            status, detail = self._delete_history(cid)
            if status == 'active':
                return
            if status == 'error':
                QMessageBox.warning(dlg, "Error", detail or "Error"); return
            if status == 'not_found':
                QMessageBox.information(dlg, "No encontrado", "La conversación ya no existe.")
            # refresh list
            lst.clear()
            for c in self._store.list_conversations():
                label = f"{c.get('model_id') or '?'} | {c.get('artifact_filename') or '?'} | {c.get('message_count',0)} msgs"
                item = QListWidgetItem(label)
                item.setData(32, c["id"])
                lst.addItem(item)
            _update_del()
        def _do_rename():
            it = lst.currentItem()
            if it is None:
                return
            cid = it.data(32)
            current_title = ""
            for c in self._store.list_conversations():
                if c["id"] == cid:
                    current_title = c.get("title") or ""
                    break
            text, ok = QInputDialog.getText(dlg, "Renombrar", "Título:", text=current_title)
            if not ok:
                return
            if not text.strip():
                QMessageBox.warning(dlg, "Error", "El título no puede estar vacío.")
                return
            try:
                renamed = self._store.rename_conversation(cid, text)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(dlg, "Error", str(exc))
                return
            if not renamed:
                QMessageBox.information(dlg, "No encontrado", "La conversación ya no existe.")
                return
            # refresh list preserving selection
            selected = cid
            lst.clear()
            for c in self._store.list_conversations():
                label = c.get("title") or f"{c.get('model_id') or '?'} | {c.get('artifact_filename') or '?'} | {c.get('message_count',0)} msgs"
                item = QListWidgetItem(label)
                item.setData(32, c["id"])
                lst.addItem(item)
                if c["id"] == selected:
                    lst.setCurrentItem(item)
            _update_del()
        rename_btn.clicked.connect(_do_rename)
        del_btn.clicked.connect(_do_delete)
        dlg.resize(400, 300)
        if dlg.exec():
            item = lst.currentItem()
            if item is None:
                return
            cid = item.data(32)
            msgs = self._store.load_conversation(cid)
            self._saved_history = self._history.toPlainText()
            self._saved_status = self._status.text()
            self._history_mode = True
            self._history.clear()
            for m in msgs:
                self._history.append(f"{m['role']}: {m['content']}")
            self._status.setText("Historial guardado (solo lectura).")
            self._prompt.setEnabled(False)
            self._send_button.setEnabled(False)
            self._back_button.setEnabled(True)
            self._continue_button.setEnabled(True)

    def _on_back_from_history(self) -> None:
        if not self._history_mode:
            return
        self._history.setPlainText(self._saved_history)
        self._status.setText(self._saved_status)
        self._history_mode = False
        self._back_button.setEnabled(False)
        has_session = self._session_id is not None
        self._send_button.setEnabled(has_session)
        self._prompt.setEnabled(has_session)

    def _on_create_continuation(self) -> None:
        if not self._history_mode:
            return
        if self._session_id is not None:
            self._status.setText("Ya hay una sesión activa; ciérrala antes de continuar.")
            return
        artifact = self._artifact
        if artifact is None:
            self._status.setText(_NO_ARTIFACT_TEXT)
            return
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton, QHBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Crear continuación")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Se abrirá una sesión nueva. El modelo no recuperará automáticamente la sesión original."))
        summary = QTextEdit(dlg)
        layout.addWidget(summary)
        btns = QHBoxLayout()
        ok = QPushButton("Crear", dlg)
        cancel = QPushButton("Cancelar", dlg)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        layout.addLayout(btns)
        ok.clicked.connect(dlg.accept)
        cancel.clicked.connect(dlg.reject)
        if not dlg.exec():
            return
        text = summary.toPlainText().strip()
        self._pending_summary = text or None
        self._history_mode = False
        self._back_button.setEnabled(False)
        self._continue_button.setEnabled(False)
        self._history.setPlainText(self._saved_history)
        self._status.setText(self._saved_status)
        accepted = self._service.open_session(artifact.model_id, filename=artifact.filename, quantization=artifact.quantization)
        if not accepted:
            self._pending_summary = None
            self._status.setText("No se pudo abrir la sesión.")
            self._update_controls()
            return
        self._history.clear()
        self._status.setText(_OPENING_TEXT)
        self._update_controls()

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
        self._conversation_id = str(uuid.uuid4())
        self._messages = []
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
            assistant_text = ""
        else:
            self._append(f"Core ({turn_count}): {response}")
            self._status.setText(f"Respuesta recibida ({turn_count} turno(s)).")
            assistant_text = response
        self._messages.append({"role": "assistant", "content": assistant_text})
        if self._store is not None and self._conversation_id is not None:
            try:
                self._store.save_conversation(
                    self._conversation_id,
                    self._messages,
                    model_id=self._artifact.model_id if self._artifact else None,
                    artifact_filename=self._artifact.filename if self._artifact else None,
                )
            except Exception:  # noqa: BLE001
                self._status.setText("Error al persistir la conversación.")

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
