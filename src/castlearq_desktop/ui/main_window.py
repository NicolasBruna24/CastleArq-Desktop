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

"""Main application window for CastleArq Desktop.

Phase 1 presents a minimal, functional window with a clear placeholder stating
that Core integration is not yet implemented. It intentionally contains no fake
model data, no simulated inference, and no misleading indication that Core is
connected.

WP1 adds only the explicit interpreter-selection seam: a button that opens a
file dialog, persists the chosen absolute path under the approved settings key,
and does nothing when cancelled. It never launches CastleArq Core.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from castlearq_desktop import APP_NAME
from castlearq_desktop.core.interpreter_settings import (
    InterpreterSettingsStore,
    create_interpreter_settings,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # runtime stays decoupled from the controller implementation
    from castlearq_desktop.core.startup_controller import StartupController

# Message shown to the user while Core integration is not implemented.
_PLACEHOLDER_TEXT = (
    "Core integration is not yet implemented.\n\n"
    "This is the Phase 1 application skeleton. Future phases will add the "
    "model library and chat screens once communication with CastleArq Core "
    "is available."
)

# Shown while no interpreter path has been explicitly selected.
_NO_SELECTION_TEXT = "No Python interpreter selected yet."

# Shown in the Core status line before any startup is requested.
_STATUS_IDLE_TEXT = "Core: not started."


class MainWindow(QMainWindow):
    """The primary application window."""

    def __init__(
        self,
        parent: QWidget | None = None,
        interpreter_settings: InterpreterSettingsStore | None = None,
        controller: "StartupController | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.resize(900, 600)

        # Tests inject a fake-backed store; production uses approved QSettings.
        self._interpreter_settings = (
            interpreter_settings
            if interpreter_settings is not None
            else create_interpreter_settings()
        )

        # Optional application controller. When present, the window delegates
        # Core startup/readiness/shutdown orchestration to it instead of owning
        # any process or HTTP concern directly.
        self._controller = controller

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel(APP_NAME)
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 6)
        title_font.setBold(True)
        title.setFont(title_font)

        placeholder = QLabel(_PLACEHOLDER_TEXT)
        placeholder.setWordWrap(True)
        placeholder.setAlignment(Qt.AlignmentFlag.AlignTop)
        placeholder.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self._interpreter_button = QPushButton("Select Python interpreter…")
        self._interpreter_button.clicked.connect(self._on_select_interpreter)

        self._interpreter_label = QLabel(
            self._interpreter_settings.load() or _NO_SELECTION_TEXT
        )
        self._interpreter_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout.addWidget(title)
        layout.addWidget(placeholder)
        layout.addWidget(self._interpreter_button)
        layout.addWidget(self._interpreter_label)

        self._status_label = QLabel(_STATUS_IDLE_TEXT)
        self._status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._start_button = QPushButton("Start Core")
        self._start_button.clicked.connect(self._on_start)
        layout.addWidget(self._start_button)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

        self.setCentralWidget(central)
        self._connect_controller()

    def _on_select_interpreter(self) -> None:
        """Persist an explicitly chosen interpreter path; cancel is a no-op.

        Only a non-empty absolute path from the dialog is accepted. Cancelling
        leaves the previous selection untouched and never starts Core.
        """
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Python interpreter",
            "",
            "All files (*)",
        )
        if not path:
            return  # cancelled: no new selection; Core is not started
        if not os.path.isabs(path):
            return  # non-absolute selections are rejected before persistence
        self._interpreter_settings.save(path)
        self._interpreter_label.setText(path)

    # -- Core startup / readiness / shutdown (delegated to the controller) --
    def _connect_controller(self) -> None:
        """Wire controller signals to the status line when a controller exists."""
        if self._controller is None:
            self._start_button.setEnabled(False)
            return
        self._controller.state_changed.connect(self._on_state_changed)
        self._controller.readiness_result.connect(self._on_readiness_result)
        self._controller.readiness_error.connect(self._on_readiness_error)

    def _on_start(self) -> None:
        """Request a single Core startup through the controller (no-op if none)."""
        if self._controller is None:
            return
        result = self._controller.start()
        self._show_startup_failure(result)

    def _show_startup_failure(self, result: object) -> None:
        """Surface a launch/validation failure code and its safe message.

        Preserves the existing lifecycle-state display; only enriches the
        status line when the operation failed and an error is attached to the
        result. Missing error objects, codes, or messages degrade gracefully.
        Only the approved error code and the actionable user message are ever
        shown — never technical context or tracebacks.
        """
        if result is None:
            return
        error = getattr(result, "error", None)
        if error is None and getattr(result, "status", None) != "failed":
            return
        code = getattr(error, "code", None)
        label = getattr(code, "value", code)
        message = ""
        if error is not None:
            try:
                message = str(error).strip()
            except Exception:
                message = ""
        if label and message:
            self._status_label.setText(f"Core: start failed ({label}). {message}")
        elif label:
            self._status_label.setText(f"Core: start failed ({label}).")
        elif message:
            self._status_label.setText(f"Core: start failed. {message}")
        else:
            self._status_label.setText("Core: start failed.")

    def _on_state_changed(self, state: object) -> None:
        self._status_label.setText(f"Core: {getattr(state, 'value', state)}")

    def _on_readiness_result(self, result: object) -> None:
        if getattr(result, "ready", False):
            self._status_label.setText("Core: ready.")
        else:
            code = getattr(result, "error_code", None)
            label = getattr(code, "value", code)
            self._status_label.setText(f"Core: not ready ({label}).")

    def _on_readiness_error(self, exc: object) -> None:
        self._status_label.setText(f"Core: readiness error ({exc}).")

    def closeEvent(self, event: object) -> None:
        """Route window close through the controller; defer while shutting down.

        When a controller is present and shutdown has not yet reached a terminal
        outcome, the close is deferred (event ignored) so the Qt event loop stays
        alive while the controller coordinates a non-blocking shutdown. The
        application exits only after the controller emits its terminal outcome.
        """
        controller = self._controller
        if controller is not None and not controller.shutdown_complete:
            controller.request_shutdown()
            event.ignore()
        else:
            event.accept()
