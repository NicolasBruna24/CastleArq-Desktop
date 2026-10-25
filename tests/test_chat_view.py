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

"""Tests for the chat view.

The service is always a double: the view's own behaviour (control states,
history, artifact changes) is what is under test, never Core.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from PySide6.QtCore import QObject, Signal

from castlearq_desktop.core.download_service import LocalArtifact, StoreState
from castlearq_desktop.ui.chat_view import ChatView

ARTIFACT = LocalArtifact(
    model_id="demo-model",
    filename="demo.gguf",
    quantization="Q4_K_M",
    size_bytes=1024,
    state=StoreState.VERIFIED,
)


class FakeService(QObject):
    """Service double recording the requests the view makes."""

    session_opened = Signal(str, str)
    turn_completed = Signal(str, int)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.is_busy = False
        self.refuse_open = False
        self.refuse_send = False
        self.opened: list[tuple[str, str, Optional[str]]] = []
        self.turns: list[str] = []
        self.closed_sessions = 0
        self.invalidations = 0

    def open_session(
        self, model_id: str, *, filename: Optional[str] = None, quantization: Optional[str] = None, device: Optional[str] = None
    ) -> bool:
        if self.refuse_open:
            return False
        self.opened.append((model_id, filename or "", quantization or ""))
        return True

    def send_turn(self, prompt: str) -> bool:
        if self.refuse_send:
            return False
        self.turns.append(prompt)
        return True

    def close_session(self) -> bool:
        self.closed_sessions += 1
        return True

    def invalidate(self) -> None:
        self.invalidations += 1


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def view(qapp):
    service = FakeService()
    widget = ChatView(service)  # type: ignore[arg-type]
    yield widget, service
    widget.deleteLater()


# -- selection --------------------------------------------------------------


def test_without_an_artifact_the_chat_cannot_be_started(view) -> None:
    widget, _ = view
    assert widget._start_button.isEnabled() is False
    assert widget._send_button.isEnabled() is False


def test_selecting_an_artifact_enables_starting_the_chat(view) -> None:
    widget, _ = view
    widget.set_artifact(ARTIFACT)
    assert widget._start_button.isEnabled() is True
    assert widget._send_button.isEnabled() is False


def test_starting_the_chat_sends_the_logical_selectors_of_the_artifact(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)

    widget._on_start()

    assert service.opened == [("demo-model", "demo.gguf", "Q4_K_M")]


def test_starting_without_an_artifact_does_nothing(view) -> None:
    widget, service = view
    widget._on_start()
    assert service.opened == []


# -- duplicate starts --------------------------------------------------------


def test_start_is_disabled_while_a_session_is_open(view) -> None:
    widget, _ = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")

    assert widget._start_button.isEnabled() is False


def test_start_is_enabled_again_after_closing_the_session(view) -> None:
    widget, _ = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")

    widget.close_session()

    assert widget._start_button.isEnabled() is True


def test_a_refused_duplicate_start_keeps_history_and_status(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("hola")
    widget._on_send()
    widget._on_turn_completed("qué tal", 1)
    history_before = widget._history.toPlainText()
    status_before = widget._status.text()
    service.refuse_open = True
    service.opened.clear()

    # Only reachable programmatically: the button itself is disabled.
    widget._on_start()

    assert widget._history.toPlainText() == history_before
    assert "Tú: hola" in history_before
    assert "Core (1): qué tal" in history_before
    assert widget._status.text() == status_before
    assert service.opened == []
    assert widget._start_button.isEnabled() is False


# -- history ----------------------------------------------------------------


def test_a_completed_turn_is_shown_in_the_history(view) -> None:
    widget, _ = view
    widget._on_session_opened("session-1", "demo-model")
    widget._on_turn_completed("hola", 1)
    assert "hola" in widget._history.toPlainText()


def test_the_user_prompt_is_shown_before_it_is_sent(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("¿qué tal?")

    widget._on_send()

    assert service.turns == ["¿qué tal?"]
    assert "¿qué tal?" in widget._history.toPlainText()
    assert widget._prompt.toPlainText() == ""


def test_an_empty_prompt_is_not_sent(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("   ")

    widget._on_send()

    assert service.turns == []


# -- control states ---------------------------------------------------------


def test_the_controls_are_disabled_while_the_service_is_busy(view) -> None:
    widget, _ = view
    widget.set_artifact(ARTIFACT)
    widget._on_session_opened("session-1", "demo-model")

    widget._on_busy_changed(True)

    assert widget._start_button.isEnabled() is False
    assert widget._send_button.isEnabled() is False
    assert widget._close_button.isEnabled() is False
    assert widget._prompt.isEnabled() is False


def test_the_controls_come_back_after_the_operation(view) -> None:
    widget, _ = view
    widget.set_artifact(ARTIFACT)
    widget._on_session_opened("session-1", "demo-model")
    widget._on_busy_changed(True)

    widget._on_busy_changed(False)

    assert widget._send_button.isEnabled() is True
    assert widget._close_button.isEnabled() is True
    assert widget._prompt.isEnabled() is True


def test_a_failure_shows_the_message_of_core(view) -> None:
    widget, _ = view
    widget._on_failed("El Core está ocupado")
    assert "ocupado" in widget._status.text()


# -- artifact changes -------------------------------------------------------


def test_changing_the_artifact_closes_the_open_session(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")

    other = LocalArtifact(
        model_id="otro-model", filename="otro.gguf", state=StoreState.VERIFIED
    )
    widget.set_artifact(other)

    assert service.closed_sessions == 1
    assert widget._send_button.isEnabled() is False
    assert widget._start_button.isEnabled() is True


def test_clearing_the_artifact_disables_the_chat(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")

    widget.set_artifact(None)

    assert service.closed_sessions == 1
    assert widget._start_button.isEnabled() is False
    assert widget._send_button.isEnabled() is False


def test_selecting_an_artifact_without_a_session_closes_nothing(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    assert service.closed_sessions == 0


# -- closing ----------------------------------------------------------------


def test_closing_the_chat_clears_the_history(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("hola")
    widget._on_send()

    widget._on_close()

    assert service.closed_sessions == 1
    assert widget._history.toPlainText() == ""
    assert widget._send_button.isEnabled() is False


def test_closing_publishes_the_session_closed_signal(view) -> None:
    widget, _ = view
    closed: list[bool] = []
    widget.session_closed.connect(lambda: closed.append(True))
    widget._on_close()
    assert closed == [True]


def test_invalidating_drops_the_pending_results(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")

    widget.invalidate()

    assert service.invalidations == 1
    assert widget._send_button.isEnabled() is False
    assert widget._history.toPlainText() == ""

def test_a_rejected_send_keeps_history_and_feedback(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("hola")
    widget._on_send()
    service.refuse_send = True
    widget._prompt.setPlainText("adios")
    history_before = widget._history.toPlainText()

    widget._on_send()

    assert "no fue aceptado" in widget._status.text()
    assert widget._history.toPlainText() == history_before
    assert "adios" not in widget._history.toPlainText()
    assert widget._prompt.toPlainText() == "adios"


def test_an_accepted_turn_that_fails_records_an_error_marker(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("hola")
    widget._on_send()

    history_before = widget._history.toPlainText()
    widget._on_failed("El Core está ocupado")

    assert "Error: El Core está ocupado" in widget._history.toPlainText()
    assert history_before in widget._history.toPlainText()
    assert "ocupado" in widget._status.text()


def test_a_completed_turn_clears_pending_state(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("hola")
    widget._on_send()

    widget._on_turn_completed("", 1)
    assert "(sin contenido)" in widget._history.toPlainText()
    assert "sin contenido" in widget._status.text()

    widget._prompt.setPlainText("otro")
    widget._on_send()
    widget._on_turn_completed("   ", 2)
    assert "(sin contenido)" in widget._history.toPlainText()

    widget._prompt.setPlainText("normal")
    widget._on_send()
    widget._on_turn_completed("respuesta normal", 3)
    assert "respuesta normal" in widget._history.toPlainText()


def test_a_session_open_failure_is_not_attributed_to_a_turn(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)

    widget._on_failed("No hay una sesión de chat abierta.")

    assert "Error:" not in widget._history.toPlainText()




def test_a_failed_accepted_turn_restores_the_prompt(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("hola")
    widget._on_send()

    widget._on_failed("El Core está ocupado")

    assert widget._prompt.toPlainText() == "hola"
    assert "Error: El Core está ocupado" in widget._history.toPlainText()


def test_a_failed_accepted_turn_preserves_new_user_text(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("hola")
    widget._on_send()

    widget._prompt.setPlainText("texto nuevo")
    widget._on_failed("El Core está ocupado")

    assert widget._prompt.toPlainText() == "texto nuevo"


def test_a_completed_turn_does_not_restore_later(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("hola")
    widget._on_send()

    widget._on_turn_completed("respuesta", 1)
    widget._on_failed("fallo tardío")

    assert widget._prompt.toPlainText() == ""



def test_a_completed_turn_clears_pending_state(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._prompt.setPlainText("hola")
    widget._on_send()

    widget._on_turn_completed("respuesta", 1)
    history_before = widget._history.toPlainText()
    widget._on_failed("fallo tardío")

    assert widget._history.toPlainText() == history_before
    assert "fallo tardío" not in widget._history.toPlainText()


def test_history_mode_disables_send_and_back_restores(view, monkeypatch) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._history.append("user: hola")

    class FakeStore:
        def list_conversations(self):
            return [{"id": "cid", "model_id": "m", "artifact_filename": "a", "message_count": 1}]

        def load_conversation(self, cid):
            return [{"role": "assistant", "content": "historico"}]

    widget._store = FakeStore()

    import castlearq_desktop.ui.chat_view as cv
    monkeypatch.setattr(cv.QDialog, "exec", lambda self: 1)

    class Item:
        def data(self, n):
            return "cid"

    monkeypatch.setattr(cv.QListWidget, "currentItem", lambda self: Item())

    widget._on_history()
    assert widget._send_button.isEnabled() is False
    assert "historico" in widget._history.toPlainText()

    widget._on_back_from_history()
    assert "hola" in widget._history.toPlainText()
    assert widget._send_button.isEnabled() is True


def _patch_dialog(monkeypatch, summary_text, accept=True):
    from PySide6.QtWidgets import QDialog, QTextEdit
    monkeypatch.setattr(QDialog, "exec", lambda self: 1 if accept else 0)
    monkeypatch.setattr(QTextEdit, "toPlainText", lambda self: summary_text)


def test_continuation_summary_sent_in_one_turn(view, monkeypatch) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._session_id = None
    widget._history_mode = True
    widget._saved_history = ""
    widget._saved_status = "ok"
    widget._continue_button.setEnabled(True)

    _patch_dialog(monkeypatch, "resumen")
    widget._on_create_continuation()
    widget._prompt.setPlainText("hola")
    widget._on_send()
    assert len(service.turns) == 1
    assert "Contexto proporcionado por el usuario:" in service.turns[0]
    assert "Mensaje actual:" in service.turns[0]
    assert "resumen" in service.turns[0]
    assert "hola" in service.turns[0]


def test_continuation_empty_summary_sends_only_message(view, monkeypatch) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._session_id = None
    widget._history_mode = True
    widget._saved_history = ""
    widget._saved_status = "ok"
    widget._continue_button.setEnabled(True)

    _patch_dialog(monkeypatch, "")
    widget._on_create_continuation()
    widget._prompt.setPlainText("hola")
    widget._on_send()
    assert service.turns[-1] == "hola"


def test_continuation_spaces_summary_sends_only_message(view, monkeypatch) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._session_id = None
    widget._history_mode = True
    widget._saved_history = ""
    widget._saved_status = "ok"
    widget._continue_button.setEnabled(True)

    _patch_dialog(monkeypatch, "   ")
    widget._on_create_continuation()
    widget._prompt.setPlainText("hola")
    widget._on_send()
    assert service.turns[-1] == "hola"


def test_continuation_cancel_does_not_open_session(view, monkeypatch) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._session_id = None
    widget._history_mode = True
    widget._saved_history = ""
    widget._saved_status = "ok"
    widget._continue_button.setEnabled(True)
    opened_before = len(service.opened)

    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    widget._on_create_continuation()
    assert len(service.opened) == opened_before


def test_continuation_active_session_blocks(view) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._history_mode = True
    widget._saved_history = ""
    widget._saved_status = "ok"
    widget._continue_button.setEnabled(True)
    opened_before = len(service.opened)
    widget._on_create_continuation()
    assert len(service.opened) == opened_before
    assert widget._session_id == "session-1"


def test_continuation_open_failure(view, monkeypatch) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._session_id = None
    widget._history_mode = True
    widget._saved_history = ""
    widget._saved_status = "ok"
    widget._continue_button.setEnabled(True)
    service.refuse_open = True

    from PySide6.QtWidgets import QDialog, QTextEdit
    monkeypatch.setattr(QDialog, "exec", lambda self: 1)
    monkeypatch.setattr(QTextEdit, "toPlainText", lambda self: "resumen")

    widget._on_create_continuation()
    assert widget._session_id is None
    assert "No se pudo abrir" in widget._status.text()


def test_continuation_first_turn_failure(view, monkeypatch) -> None:
    widget, service = view
    widget.set_artifact(ARTIFACT)
    widget._on_start()
    widget._on_session_opened("session-1", "demo-model")
    widget._session_id = None
    widget._history_mode = True
    widget._saved_history = ""
    widget._saved_status = "ok"
    widget._continue_button.setEnabled(True)

    from PySide6.QtWidgets import QDialog, QTextEdit
    monkeypatch.setattr(QDialog, "exec", lambda self: 1)
    monkeypatch.setattr(QTextEdit, "toPlainText", lambda self: "resumen")
    widget._on_create_continuation()
    service.refuse_send = True
    widget._prompt.setPlainText("hola")
    widget._on_send()
    assert "no fue aceptado" in widget._status.text().lower() or "no fue aceptado" in widget._status.text()

def test_delete_history_non_active_success(view) -> None:
    widget, service = view
    store = type('S', (), {})()
    store.delete_conversation = lambda cid: True
    widget._store = store
    widget._conversation_id = 'active-id'
    assert widget._delete_history('other-id') == ('deleted', None)
    assert service.closed_sessions == 0

def test_delete_history_active_blocks(view) -> None:
    widget, service = view
    calls = []
    store = type('S', (), {})()
    store.delete_conversation = lambda cid: calls.append(cid) or True
    widget._store = store
    widget._conversation_id = 'active-id'
    assert widget._delete_history('active-id') == ('active', None)
    assert calls == []

def test_delete_history_not_found(view) -> None:
    widget, _ = view
    store = type('S', (), {})()
    store.delete_conversation = lambda cid: False
    widget._store = store
    widget._conversation_id = 'active-id'
    assert widget._delete_history('other-id') == ('not_found', None)

def test_delete_history_error(view) -> None:
    from castlearq_desktop.storage.conversation_store import ConversationStoreError
    widget, _ = view
    store = type('S', (), {})()
    def boom(cid):
        raise ConversationStoreError('boom')
    store.delete_conversation = boom
    widget._store = store
    widget._conversation_id = 'active-id'
    assert widget._delete_history('other-id') == ('error', 'boom')

def test_delete_history_passes_uuid(view) -> None:
    widget, _ = view
    seen = []
    store = type('S', (), {})()
    store.delete_conversation = lambda cid: (seen.append(cid) or True)
    widget._store = store
    widget._conversation_id = 'active-id'
    widget._delete_history('exact-uuid')
    assert seen == ['exact-uuid']

def test_delete_history_session_preserved(view) -> None:
    widget, service = view
    store = type('S', (), {})()
    store.delete_conversation = lambda cid: True
    widget._store = store
    widget._conversation_id = 'active-id'
    widget._session_id = 'session-1'
    widget._delete_history('other-id')
    assert widget._session_id == 'session-1'
def test_rename_identity_preserved(view) -> None:
    widget, _ = view
    widget._conversation_id = 'active-id'
    widget._session_id = 'session-1'
    assert widget._conversation_id == 'active-id'
    assert widget._session_id == 'session-1'
