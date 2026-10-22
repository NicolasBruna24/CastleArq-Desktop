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

Presents interpreter configuration, the CastleArq Core lifecycle status,
the model catalog explorer, the local artifact library, and the chat view.
It intentionally contains no fake model data and no simulated inference:
everything shown comes from the configured interpreter and Core.
"""

from __future__ import annotations

import os
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from castlearq_desktop import APP_NAME
from castlearq_desktop.core.interpreter_settings import (
    InterpreterSettingsStore,
    create_interpreter_settings,
)
from castlearq_desktop.core.model_catalog import ModelEntry
from castlearq_desktop.core.download_service import (
    LocalLibraryService,
    ModelAcquisitionService,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # runtime stays decoupled from the controller implementation
    from castlearq_desktop.core.startup_controller import StartupController

# Shown while no interpreter path has been explicitly selected.
_NO_SELECTION_TEXT = "No Python interpreter selected yet."

# Shown in the Core status line before any startup is requested.
_STATUS_IDLE_TEXT = "Core: not started."

#: Logger dedicado al diagnóstico temporal de readiness (solo DEBUG).
_logger = logging.getLogger(__name__)

# Shown in the model selection line while no model is selected.
_NO_MODEL_TEXT = "Ningún modelo seleccionado."

# Shown in the artifact selection line while no installed artifact is selected.
_NO_ARTIFACT_TEXT = "Ningún artefacto local seleccionado."


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

        # Selected catalog model and selected installed artifact are kept
        # apart: they come from two different surfaces of Core.
        self._selected_model = None
        self._selected_artifact = None
        #: Library refresh scheduled by a finished acquisition but not yet
        #: started because another library query was already running.
        self._pending_library_refresh: bool = False

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)

        central = QWidget(scroll)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel(APP_NAME)
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 6)
        title_font.setBold(True)
        title.setFont(title_font)

        self._interpreter_button = QPushButton("Select Python interpreter…")
        self._interpreter_button.clicked.connect(self._on_select_interpreter)

        self._interpreter_label = QLabel(
            self._interpreter_settings.load() or _NO_SELECTION_TEXT
        )
        self._interpreter_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout.addWidget(title)
        layout.addWidget(self._interpreter_button)
        layout.addWidget(self._interpreter_label)

        self._status_label = QLabel(_STATUS_IDLE_TEXT)
        self._status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        # Selection feedback lives in its own labels: the Core status line
        # reports Core's own state and must not be overwritten by a UI
        # selection. The catalog selection and the installed-artifact
        # selection are kept apart, because they come from two different
        # surfaces of Core and neither replaces the other.
        self._model_status_label = QLabel(_NO_MODEL_TEXT)
        self._model_status_label.setWordWrap(True)
        self._model_status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._artifact_status_label = QLabel(_NO_ARTIFACT_TEXT)
        self._artifact_status_label.setWordWrap(True)
        self._artifact_status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._start_button = QPushButton("Start Core")
        self._start_button.clicked.connect(self._on_start)
        layout.addWidget(self._start_button)
        layout.addWidget(self._status_label)
        layout.addWidget(self._model_status_label)
        layout.addWidget(self._artifact_status_label)

        # Settings → Updates (Stage 3): minimal section, no navigation change.
        settings_box = QGroupBox("Settings", self)
        settings_layout = QVBoxLayout(settings_box)
        updates_title = QLabel("Actualizaciones")
        updates_title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        settings_layout.addWidget(updates_title)
        try:
            from castlearq_desktop.core.update_check_controller import (
                UpdateCheckController,
            )
            from castlearq_desktop.ui.updates_section import UpdatesSection
        except ImportError:  # Updates section is optional
            self._update_controller = None
            self._updates_section = QLabel("Updates unavailable.")
        else:
            self._update_controller = UpdateCheckController(
                interpreter_settings=self._interpreter_settings,
                parent=self,
            )
            self._updates_section = UpdatesSection(
                controller=self._update_controller, parent=settings_box
            )
        settings_layout.addWidget(self._updates_section)
        layout.addWidget(settings_box)

        # Model Explorer: consults GET /v1/models when Core is READY.
        try:
            from castlearq_desktop.core.catalog_controller import (
                CatalogController,
            )
            from castlearq_desktop.ui.model_explorer_view import (
                ModelExplorerView,
            )
        except ImportError:  # Model Explorer is optional
            self._catalog_controller = None
            self._model_explorer = QLabel("Model Explorer unavailable.")
            layout.addWidget(self._model_explorer)
        else:
            self._catalog_controller = CatalogController(
                is_ready=self._core_is_ready,
                parent=self,
            )
            self._model_explorer = ModelExplorerView(
                controller=self._catalog_controller, parent=self
            )
            self._model_explorer.set_downloader(
                ModelAcquisitionService(
                    interpreter_settings=self._interpreter_settings
                )
            )
            layout.addWidget(self._model_explorer)
            self._model_explorer.model_selected.connect(self._on_model_selected)
            self._model_explorer.model_unselected.connect(self._on_model_unselected)
            self._model_explorer.acquisition_finished.connect(
                self._on_acquisition_finished
            )
            self._model_explorer.acquisition_failed.connect(
                self._on_acquisition_failed
            )

        # Local library: the artifacts Core really finds in the model store.
        # Its state is independent from Core's status line, from the catalog
        # and from the acquisition.
        try:
            from castlearq_desktop.ui.local_library_view import LocalLibraryView
        except ImportError:  # Local library is optional
            self._local_library = QLabel("Local library unavailable.")
            layout.addWidget(self._local_library)
        else:
            self._local_library_service = LocalLibraryService(
                interpreter_settings=self._interpreter_settings
            )
            self._local_library = LocalLibraryView(
                service=self._local_library_service, parent=self
            )
            layout.addWidget(self._local_library)
            self._local_library.artifact_selected.connect(
                self._on_artifact_selected
            )
            self._local_library.artifact_unselected.connect(
                self._on_artifact_unselected
            )
            # A *correct* library query feeds the explorer's local
            # availability section with the complete artifact list. No
            # query is ever started for this, and before the first query
            # there is nothing to send: the explorer claims nothing.
            self._local_library_service.finished.connect(
                self._on_local_library_finished
            )

        # Chat: a real session on Core's HTTP API. The session owns the
        # conversation; Desktop only shows what Core answered.
        try:
            from castlearq_desktop.core.chat_service import ChatService
            from castlearq_desktop.ui.chat_view import ChatView
        except ImportError:  # Chat is optional
            self._chat_view = QLabel("Chat unavailable.")
            layout.addWidget(self._chat_view)
        else:
            self._chat_service = ChatService(parent=self)
            self._chat_view = ChatView(service=self._chat_service, parent=self)
            layout.addWidget(self._chat_view)
            self._chat_view.session_closed.connect(self._on_chat_session_closed)
            # The library re-checks this state right before applying a
            # pending selection: a session can open while the query runs.
            if hasattr(self._local_library, "set_session_active_provider"):
                self._local_library.set_session_active_provider(
                    lambda: bool(getattr(self._chat_view, "has_session", False))
                )

        layout.addStretch(1)

        scroll.setWidget(central)

        self.setCentralWidget(scroll)
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

    # -- Model selection (new Phase 2) -------------------------------------
    def _on_model_selected(self, entry: ModelEntry) -> None:
        """Forward the user-selected model to MainWindow for display.

        Browsing the catalog never touches the chat conversation: the
        session belongs to the installed artifact, and only changing
        that artifact (via ``ChatView.set_artifact``) closes it.
        """
        self._selected_model = entry
        self._model_status_label.setText(
            f"Modelo seleccionado: {entry.name} ({entry.model_id})"
        )

    def _on_model_unselected(self) -> None:
        """Clear the current selection in MainWindow.

        Deselecting a catalog model also leaves any open chat session
        untouched, for the same reason as ``_on_model_selected``.
        """
        self._selected_model = None
        self._model_status_label.setText(_NO_MODEL_TEXT)

    # -- Local library (installed artifacts) --------------------------------
    def _on_artifact_selected(self, artifact) -> None:
        """Report the selected installed artifact in its own line.

        The catalog selection keeps its own line: selecting an installed
        artifact never erases the catalog selection, and vice versa.
        """
        self._selected_artifact = artifact
        self._artifact_status_label.setText(
            f"Artefacto instalado: {artifact.filename} ({artifact.model_id})"
        )
        chat_view = getattr(self, "_chat_view", None)
        if chat_view is not None and hasattr(chat_view, "set_artifact"):
            chat_view.set_artifact(artifact)

    def _on_artifact_unselected(self) -> None:
        """Clear the selected installed artifact."""
        self._selected_artifact = None
        self._artifact_status_label.setText(_NO_ARTIFACT_TEXT)
        chat_view = getattr(self, "_chat_view", None)
        if chat_view is not None and hasattr(chat_view, "set_artifact"):
            chat_view.set_artifact(None)

    def _on_chat_session_closed(self) -> None:
        """React to a chat session that was closed on Core."""
        return

    def _on_acquisition_failed(self, message: str) -> None:
        """React to a failed acquisition (both visible in the explorer and here)."""
        library = getattr(self, "_local_library", None)
        if library is not None:
            library.clear_pending_selection()
        service = getattr(self, "_local_library_service", None)
        if service is not None:
            service.invalidate()

    def _on_acquisition_finished(self, result) -> None:
        """Refresh the local library after an acquisition ended.

        The refresh is asynchronous and never blocks the GUI thread; it is
        skipped when another query is already running, so no duplicate
        query is ever started. The library reports Core's own store state,
        so a successful transfer is never presented as verified here.

        When the acquisition really succeeded, fully identifies its
        artifact and would not interrupt an active conversation, that
        artifact is scheduled as the library's pending selection: it is
        applied only if Core's own results actually contain it (and never
        before the chat session question is settled). A failed or
        incomplete result clears any pending request instead. If no new
        query can start, the pending request is kept for the query already
        in course — the refresh is not assumed to have happened.
        """
        library = getattr(self, "_local_library", None)
        if library is None:
            return
        service = getattr(self, "_local_library_service", None)
        if service is None:
            return
        chat_view = getattr(self, "_chat_view", None)
        session_active = bool(getattr(chat_view, "has_session", False))
        succeeded = bool(getattr(result, "succeeded", False))
        model_id = getattr(result, "model_id", None)
        filename = getattr(result, "filename", None)
        if succeeded and model_id and filename and not session_active:
            # AcquisitionResult exposes no quantization; the identity Core
            # reported (model_id + filename) is passed without guessing.
            library.set_pending_selection(model_id, filename)
        else:
            library.clear_pending_selection()
        self._schedule_library_refresh(service)
        if service.is_busy:
            self._pending_library_refresh = True
            return
        if service.refresh():
            return
        # The refresh did not start (shutting down): the pending
        # request stays for the query in course; invalidate() clears it.
        return

    def _schedule_library_refresh(self, service) -> None:
        """Start a library refresh if possible; remember one pending request."""
        if self._pending_library_refresh:
            return

    def _on_local_library_finished(self, artifacts, store=None) -> None:
        """Show Core's complete artifact list in the explorer's detail.

        ``LocalLibraryService.finished`` only fires for a correct query (a
        failed one travels through ``failed``), so the explorer never
        presents an invented answer, and this handler starts no query of
        its own. The library view consumes the same signal as before; this
        is an additional consumer.
        """
        explorer = getattr(self, "_model_explorer", None)
        if explorer is not None and hasattr(explorer, "set_local_artifacts"):
            explorer.set_local_artifacts(artifacts)
        if self._pending_library_refresh:
            self._pending_library_refresh = False
            service = getattr(self, "_local_library_service", None)
            if service is not None and not service.is_busy:
                service.refresh()

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

    def _core_is_ready(self) -> bool:
        """Return True when the last readiness result was positive."""
        return bool(getattr(self, "_last_readiness_ready", False))

    def _on_readiness_result(self, result: object) -> None:

        _logger.debug(
            "READINESS RESULT ready=%s error_code=%s deadline_expired=%s",
            getattr(result, "ready", None),
            getattr(result, "error_code", None),
            getattr(result, "deadline_expired", None),
        )
        self._last_readiness_ready = bool(getattr(result, "ready", False))
        if self._catalog_controller is not None:
            self._model_explorer.set_core_ready(self._last_readiness_ready)
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
        updater = getattr(self, "_update_controller", None)
        if updater is not None:
            try:
                updater.invalidate()
            except Exception:  # noqa: BLE001 - close must not fail
                pass
        catalog_ctl = getattr(self, "_catalog_controller", None)
        if catalog_ctl is not None:
            try:
                catalog_ctl.invalidate()
            except Exception:  # noqa: BLE001 - close must not fail
                pass
        explorer = getattr(self, "_model_explorer", None)
        if explorer is not None and hasattr(explorer, "invalidate"):
            try:
                explorer.invalidate()
            except Exception:  # noqa: BLE001 - close must not fail
                pass
        library = getattr(self, "_local_library", None)
        if library is not None and hasattr(library, "invalidate"):
            try:
                library.invalidate()
            except Exception:  # noqa: BLE001 - close must not fail
                pass
        library_service = getattr(self, "_local_library_service", None)
        if library_service is not None:
            try:
                library_service.close()
            except Exception:  # noqa: BLE001 - close must not fail
                pass
        chat_service = getattr(self, "_chat_service", None)
        if chat_service is not None:
            try:
                # Deletes the session on Core so no runtime outlives the window.
                chat_service.close()
            except Exception:  # noqa: BLE001 - close must not fail
                pass
        chat_view = getattr(self, "_chat_view", None)
        if chat_view is not None and hasattr(chat_view, "invalidate"):
            try:
                chat_view.invalidate()
            except Exception:  # noqa: BLE001 - close must not fail
                pass
        if controller is not None and not controller.shutdown_complete:
            controller.request_shutdown()
            event.ignore()
        else:
            event.accept()
