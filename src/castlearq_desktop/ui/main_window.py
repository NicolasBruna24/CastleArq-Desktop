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

# Message shown to the user while Core integration is not implemented.
_PLACEHOLDER_TEXT = (
    "Core integration is not yet implemented.\n\n"
    "This is the Phase 1 application skeleton. Future phases will add the "
    "model library and chat screens once communication with CastleArq Core "
    "is available."
)

# Shown while no interpreter path has been explicitly selected.
_NO_SELECTION_TEXT = "No Python interpreter selected yet."


class MainWindow(QMainWindow):
    """The primary application window."""

    def __init__(
        self,
        parent: QWidget | None = None,
        interpreter_settings: InterpreterSettingsStore | None = None,
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
        layout.addStretch(1)

        self.setCentralWidget(central)

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
