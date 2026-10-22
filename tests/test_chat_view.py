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
        self, model_id: str, *, filename: Optional[str] = None, quantization: Optional[str] = None
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
