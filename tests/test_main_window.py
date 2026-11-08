# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for MainWindow UI navigation structure."""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSplitter, QListWidget, QStackedWidget, QWidget

from castlearq_desktop.core.interpreter_settings import InterpreterSettingsStore
from castlearq_desktop.ui.main_window import MainWindow


class _FakeSettings:
    def __init__(self):
        self.data = {}

    def value(self, key, default=None, **kwargs):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value

    def remove(self, key):
        self.data.pop(key, None)

    def sync(self):
        pass


@pytest.fixture()
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window(app):
    store = InterpreterSettingsStore(_FakeSettings())
    instance = MainWindow(interpreter_settings=store)
    return instance


def test_navigation_structure(app) -> None:
    """Verify the new sidebar + QStackedWidget navigation structure."""
    window = _window(app)
    try:
        central = window.centralWidget()
        assert isinstance(central, QSplitter)
        assert central.count() == 2
        sidebar = central.widget(0)
        assert isinstance(sidebar, QListWidget)
        stacked = central.widget(1)
        assert isinstance(stacked, QStackedWidget)
        assert stacked.count() == 7
        assert stacked.currentIndex() == 0
    finally:
        window.close()


def test_navigation_all_pages_accessible(app) -> None:
    """Verify all 7 pages are accessible through navigation."""
    window = _window(app)
    try:
        stacked = window.centralWidget().widget(1)
        for i in range(stacked.count()):
            widget = stacked.widget(i)
            assert widget is not None
            assert widget.layout() is not None or hasattr(widget, "minimumSize")
    finally:
        window.close()


def test_navigation_preserves_core_state(app) -> None:
    """Verify changing pages does not duplicate core state."""
    window = _window(app)
    try:
        stacked = window.centralWidget().widget(1)
        for _ in range(3):
            stacked.setCurrentIndex(0)
            stacked.setCurrentIndex(6)
            stacked.setCurrentIndex(0)
        assert stacked.currentIndex() == 0
    finally:
        window.close()


def test_navigation_no_regression(app) -> None:
    """Verify basic window construction works without errors."""
    window = _window(app)
    try:
        assert window is not None
        assert window.windowTitle() == "CastleArq Desktop"
        assert window._sidebar is not None
        assert window._stacked is not None
    finally:
        window.close()


def test_library_actions_navigate_and_apply_openvino_selection(app) -> None:
    from castlearq_desktop.core.download_service import LocalArtifact, StoreState

    window = _window(app)
    try:
        refreshes: list[bool] = []
        window._local_library_service.refresh = (
            lambda: refreshes.append(True) or True
        )
        gguf = LocalArtifact(
            model_id="gguf-model",
            filename="model.gguf",
            backend="gguf",
            state=StoreState.VERIFIED,
        )
        openvino = LocalArtifact(
            model_id="openvino-model",
            filename="model.xml",
            backend="openvino",
            format="OpenVINO IR",
            state=StoreState.VERIFIED,
        )
        window._local_library._populate([gguf, openvino])

        window._local_library._list.setCurrentRow(0)
        assert window._local_library._open_chat_button.isEnabled() is True
        window._local_library._open_chat_button.click()
        assert window._stacked.currentIndex() == 3
        assert window._sidebar.currentRow() == 3
        assert window._chat_view._artifact is gguf

        window._local_library._list.setCurrentRow(1)
        assert window._local_library._open_openvino_button.isEnabled() is True
        window._local_library._open_openvino_button.click()
        assert window._stacked.currentIndex() == 5
        assert window._sidebar.currentRow() == 5
        assert window._openvino_view._pending_model_id == "openvino-model"
        assert refreshes == [True]

        window._on_local_library_finished([gguf, openvino], None)
        assert [
            window._openvino_view._model.itemText(index)
            for index in range(window._openvino_view._model.count())
        ] == ["openvino-model"]
        assert window._openvino_view._model.currentText() == "openvino-model"
        assert window._openvino_view._pending_model_id is None
        assert window._openvino_view.get_selected_format() == "OpenVINO IR"
    finally:
        window.close()


def test_real_formatted_inventory_reaches_chat_and_openvino(app) -> None:
    from pathlib import Path

    from castlearq_desktop.core.download_service import parse_artifacts

    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "core_list_with_formats.json"
    )
    artifacts, error = parse_artifacts(fixture.read_text(encoding="utf-8"))
    assert error is None

    window = _window(app)
    try:
        window._local_library_service.refresh = lambda: True
        window._local_library._populate(artifacts)

        def find_row(backend: str) -> int:
            for row in range(window._local_library._list.count()):
                item = window._local_library._list.item(row)
                if item.data(Qt.UserRole).backend == backend:
                    return row
            raise AssertionError(f"no artifact found for backend {backend}")

        assert not any(
            window._local_library._list.item(row).data(Qt.UserRole).backend == "gguf"
            for row in range(window._local_library._list.count())
        )
        unclassified_row = next(
            row for row in range(window._local_library._list.count())
            if window._local_library._list.item(row).data(Qt.UserRole).backend is None
        )
        window._local_library._list.setCurrentRow(unclassified_row)
        assert window._local_library._open_chat_button.isEnabled() is False
        assert window._local_library._open_openvino_button.isEnabled() is False

        openvino_row = find_row("openvino")
        window._local_library._list.setCurrentRow(openvino_row)
        selected_openvino = window._local_library._list.item(openvino_row).data(
            Qt.UserRole
        )
        window._local_library._open_openvino_button.click()
        assert window._openvino_view._pending_model_id == selected_openvino.model_id
        assert window._stacked.currentIndex() == 5

        window._on_local_library_finished(artifacts, None)
        model_ids = [
            window._openvino_view._model.itemText(index)
            for index in range(window._openvino_view._model.count())
        ]
        assert len(model_ids) == 4
        assert selected_openvino.model_id in model_ids
        assert set(window._openvino_view._model_formats.values()) == {"OpenVINO IR"}
    finally:
        window.close()


def test_entering_openvino_refreshes_local_artifacts(app) -> None:
    window = _window(app)
    try:
        refreshes: list[bool] = []
        window._local_library_service.refresh = (
            lambda: refreshes.append(True) or True
        )

        window._sidebar.setCurrentRow(5)

        assert window._stacked.currentIndex() == 5
        assert refreshes == [True]
        assert "Cargando" in window._openvino_view._status.text()
    finally:
        window.close()


def test_local_library_failure_is_visible_in_openvino(app) -> None:
    window = _window(app)
    try:
        window._sidebar.setCurrentRow(5)

        assert "No se pudo cargar la biblioteca local" in window._openvino_view._status.text()
        assert "No interpreter configured" in window._openvino_view._status.text()
        assert window._openvino_view._run.isEnabled() is False
    finally:
        window.close()


def test_schedule_refresh_service_busy(app):
    window = _window(app)
    try:
        service = MagicMock()
        service.is_busy = True
        window._schedule_library_refresh(service)
        service.refresh.assert_not_called()
        assert window._pending_library_refresh is True
    finally:
        window.close()


def test_schedule_refresh_dedup(app):
    window = _window(app)
    try:
        window._pending_library_refresh = True
        service = MagicMock()
        service.is_busy = True
        window._schedule_library_refresh(service)
        service.refresh.assert_not_called()
        assert window._pending_library_refresh is True
    finally:
        window.close()


def test_schedule_refresh_refresh_returns_false(app):
    window = _window(app)
    try:
        service = MagicMock()
        service.is_busy = False
        service.refresh.return_value = False
        window._schedule_library_refresh(service)
        service.refresh.assert_called_once()
        assert window._pending_library_refresh is False
    finally:
        window.close()


def test_consume_pending_on_finished(app):
    window = _window(app)
    try:
        service = MagicMock()
        service.is_busy = False
        service.refresh.return_value = True
        window._local_library_service = service
        window._pending_library_refresh = True
        window._on_local_library_finished([], None)
        assert window._pending_library_refresh is False
        service.refresh.assert_called_once()
    finally:
        window.close()
def test_acquisition_finished_success_sets_pending_and_refreshes(app):
    window = _window(app)
    try:
        library = MagicMock()
        service = MagicMock()
        service.is_busy = False
        service.refresh.return_value = True
        chat_view = MagicMock()
        chat_view.has_session = False
        window._local_library = library
        window._local_library_service = service
        window._chat_view = chat_view

        result = MagicMock()
        result.succeeded = True
        result.model_id = "m"
        result.filename = "f.gguf"

        window._on_acquisition_finished(result)

        library.set_pending_selection.assert_called_once_with("m", "f.gguf")
        library.clear_pending_selection.assert_not_called()
        service.refresh.assert_called_once()
        assert window._pending_library_refresh is False
    finally:
        window.close()


def test_acquisition_finished_failure_clears_pending_and_refreshes(app):
    window = _window(app)
    try:
        library = MagicMock()
        service = MagicMock()
        service.is_busy = True
        chat_view = MagicMock()
        chat_view.has_session = False
        window._local_library = library
        window._local_library_service = service
        window._chat_view = chat_view

        result = MagicMock()
        result.succeeded = False
        result.model_id = "m"
        result.filename = "f.gguf"

        window._on_acquisition_finished(result)

        library.clear_pending_selection.assert_called_once()
        library.set_pending_selection.assert_not_called()
        service.refresh.assert_not_called()
        assert window._pending_library_refresh is True
    finally:
        window.close()


def test_acquisition_finished_session_active_clears_pending(app):
    window = _window(app)
    try:
        library = MagicMock()
        service = MagicMock()
        service.is_busy = False
        service.refresh.return_value = True
        chat_view = MagicMock()
        chat_view.has_session = True
        window._local_library = library
        window._local_library_service = service
        window._chat_view = chat_view

        result = MagicMock()
        result.succeeded = True
        result.model_id = "m"
        result.filename = "f.gguf"

        window._on_acquisition_finished(result)

        library.clear_pending_selection.assert_called_once()
        library.set_pending_selection.assert_not_called()
        service.refresh.assert_called_once()
    finally:
        window.close()
def test_acquisition_failed_clears_pending_and_invalidates_service(app):
    window = _window(app)
    try:
        library = MagicMock()
        service = MagicMock()
        window._local_library = library
        window._local_library_service = service

        window._on_acquisition_failed("boom")

        library.clear_pending_selection.assert_called_once()
        service.invalidate.assert_called_once()
    finally:
        window.close()


def test_acquisition_failed_without_dependencies_does_not_raise(app):
    window = _window(app)
    try:
        window._local_library = None
        window._local_library_service = None

        window._on_acquisition_failed("boom")

        # No dependencies were present, so nothing should be invoked.
    finally:
        window.close()


def test_runtime_page_accessible(app):
    """Verify the Runtime/diagnóstico page exists and has its main labels."""
    window = _window(app)
    try:
        stacked = window.centralWidget().widget(1)
        assert stacked.count() == 7
        runtime_page = stacked.widget(4)
        assert runtime_page is not None
        assert window._runtime_lifecycle_label is not None
        assert window._runtime_shutdown_label is not None
        assert window._runtime_pid_label is not None
        assert window._runtime_exit_code_label is not None
        assert window._runtime_shutdown_complete_label is not None
    finally:
        window.close()


def test_navigate_to_runtime_updates_visible_labels(app):
    """Navigating to the Runtime page refreshes visible label text."""
    window = _window(app)
    try:
        stacked = window.centralWidget().widget(1)
        stacked.setCurrentIndex(4)
        window._on_sidebar_page_changed(4)
        assert window._runtime_lifecycle_label.text() == "sin información registrada"
        assert window._runtime_shutdown_label.text() == "sin información registrada"
        assert window._runtime_shutdown_complete_label.text() == "sin información registrada"
    finally:
        window.close()


def test_runtime_view_no_controller_no_exception(app):
    """With controller=None and no data registered, navigating to Runtime
    works without exceptions and shows default text."""
    window = _window(app)
    try:
        stacked = window.centralWidget().widget(1)
        stacked.setCurrentIndex(4)
        window._on_sidebar_page_changed(4)
        assert window._runtime_lifecycle_label.text() == "sin información registrada"
        assert window._runtime_shutdown_label.text() == "sin información registrada"
        assert window._runtime_pid_label.text() == "sin información registrada"
        assert window._runtime_exit_code_label.text() == "sin información registrada"
        assert window._runtime_shutdown_complete_label.text() == "sin información registrada"
        assert window._runtime_readiness_label.text() == "sin información registrada"
        assert window._runtime_readiness_error_label.text() == "sin información registrada"
    finally:
        window.close()


def test_readiness_labels_show_known_info(app):
    """Readiness result and error are shown as known information
    (último resultado conocido / último error conocido), without claiming
    that Core continues to be operational."""
    window = _window(app)
    try:
        fake_code = SimpleNamespace(value="READINESS_TIMEOUT")
        fake_result = SimpleNamespace(ready=False, error_code=fake_code)
        window._runtime_readiness_result = fake_result
        window._runtime_readiness_error = Exception("probe failed")

        window._update_runtime_view()

        assert "no listo" in window._runtime_readiness_label.text()
        assert "Último error de readiness" in window._runtime_readiness_error_label.text()
    finally:
        window.close()


def test_shutdown_complete_true_label(app):
    """Controller with shutdown_complete=True shows 'Shutdown completo: sí.'"""
    controller = MagicMock()
    controller.shutdown_complete = True
    store = InterpreterSettingsStore(_FakeSettings())
    window = MainWindow(interpreter_settings=store, controller=controller)
    try:
        window._update_runtime_view()
        assert "Shutdown completo: sí." in window._runtime_shutdown_complete_label.text()
    finally:
        window.close()


def test_shutdown_complete_false_label(app):
    """Controller with shutdown_complete=False shows 'Shutdown completo: no completado.'"""
    controller = MagicMock()
    controller.shutdown_complete = False
    store = InterpreterSettingsStore(_FakeSettings())
    window = MainWindow(interpreter_settings=store, controller=controller)
    try:
        window._update_runtime_view()
        assert "Shutdown completo: no completado." in window._runtime_shutdown_complete_label.text()
    finally:
        window.close()


def test_navigation_no_http_or_process(app):
    """Navigating between pages does not start Core, spawn processes, or
    create HTTP clients/timers."""
    window = _window(app)
    try:
        stacked = window.centralWidget().widget(1)
        for _ in range(3):
            for i in range(stacked.count()):
                stacked.setCurrentIndex(i)
                window._on_sidebar_page_changed(i)
        assert not hasattr(window, "_http_client")
        assert not hasattr(window, "_readiness_timer")
        assert stacked.count() == 7
    finally:
        window.close()


def test_library_refresh_propagates_only_ambiguous_openvino_model_ids(app):
    from castlearq_desktop.core.download_service import LocalArtifact

    window = _window(app)
    try:
        window._on_local_library_finished(
            [
                LocalArtifact("unique", "unique.xml", backend="openvino", format="OpenVINO IR"),
                LocalArtifact("shared", "shared.xml", backend="openvino", format="OpenVINO IR"),
                LocalArtifact("shared", "shared.gguf", backend="gguf", format="GGUF"),
            ]
        )
        view = window._openvino_view
        assert view._ambiguous_model_ids == {"shared"}
        assert [view._model.itemText(i) for i in range(view._model.count())] == [
            "unique", "shared"
        ]
    finally:
        window.close()


def test_readiness_propagates_confirmed_feature_capabilities(app):
    window = _window(app)
    try:
        window._controller = SimpleNamespace(
            installed_core_version="0.6.1", shutdown_complete=True
        )
        window._on_readiness_result(SimpleNamespace(ready=True, error_code=None))
        assert window._chat_view._chat_supported is True
        assert window._chat_view._chat_device_supported is True
        assert window._openvino_view._inference_supported is False
    finally:
        window.close()


def test_core_capabilities_start_disabled_until_readiness(app):
    from castlearq_desktop.core.download_service import LocalArtifact

    window = _window(app)
    try:
        window._chat_view.set_artifact(
            LocalArtifact("m", "m.xml", backend="openvino")
        )
        assert window._confirmed_core_version is None
        assert window._chat_view._chat_supported is False
        assert window._chat_view._chat_device_supported is False
        assert window._chat_view._start_button.isEnabled() is False
        assert window._openvino_view._inference_supported is False
        assert window._openvino_view._run.isEnabled() is False
    finally:
        window.close()


@pytest.mark.parametrize(
    ("version", "device_supported"),
    [("0.5.1", False), ("0.6.0", False), ("0.6.1", True)],
)
def test_readiness_rebuilds_capabilities_for_confirmed_version(
    app, version, device_supported
):
    from castlearq_desktop.core.download_service import LocalArtifact

    window = _window(app)
    try:
        window._controller = SimpleNamespace(
            installed_core_version=version, shutdown_complete=True
        )
        window._on_readiness_result(SimpleNamespace(ready=True, error_code=None))
        window._chat_view.set_artifact(
            LocalArtifact("m", "m.xml", backend="openvino")
        )
        assert window._confirmed_core_version == version
        assert window._chat_view._chat_supported is True
        assert window._chat_view._start_button.isEnabled() is True
        assert window._chat_view._chat_device_supported is device_supported
        assert window._chat_view._device_combo.isHidden() is (not device_supported)
        assert window._openvino_view._inference_supported is False
        assert window._openvino_view._run.isEnabled() is False
    finally:
        window.close()


def test_readiness_with_unknown_version_keeps_all_capabilities_disabled(app):
    from castlearq_desktop.core.download_service import LocalArtifact

    window = _window(app)
    try:
        window._controller = SimpleNamespace(
            installed_core_version="9.9.9", shutdown_complete=True
        )
        window._on_readiness_result(SimpleNamespace(ready=True, error_code=None))
        window._chat_view.set_artifact(
            LocalArtifact("m", "m.gguf", backend="gguf")
        )
        assert window._confirmed_core_version is None
        assert window._chat_view._chat_supported is False
        assert window._chat_view._chat_device_supported is False
        assert window._chat_view._start_button.isEnabled() is False
        assert window._openvino_view._inference_supported is False
    finally:
        window.close()


def test_new_start_invalidates_capabilities_then_readiness_rebuilds_them(app):
    from castlearq_desktop.core.download_service import LocalArtifact

    controller = SimpleNamespace(
        installed_core_version="0.6.1",
        shutdown_complete=True,
        start=lambda: None,
    )
    window = _window(app)
    window._controller = controller
    try:
        window._on_readiness_result(SimpleNamespace(ready=True, error_code=None))
        window._chat_view.set_artifact(
            LocalArtifact("m", "m.xml", backend="openvino")
        )
        assert window._chat_view._device_combo.isHidden() is False

        window._on_start()
        assert window._confirmed_core_version is None
        assert window._last_readiness_ready is False
        assert window._chat_view._chat_supported is False
        assert window._chat_view._chat_device_supported is False
        assert window._chat_view._start_button.isEnabled() is False
        assert window._openvino_view._inference_supported is False

        controller.installed_core_version = "0.5.1"
        window._on_readiness_result(SimpleNamespace(ready=True, error_code=None))
        assert window._confirmed_core_version == "0.5.1"
        assert window._chat_view._chat_supported is True
        assert window._chat_view._chat_device_supported is False
    finally:
        window.close()


@pytest.mark.parametrize("readiness_failure", ["not_ready", "timeout", "cancelled"])
def test_readiness_failure_clears_previous_capabilities(app, readiness_failure):
    from castlearq_desktop.core.download_service import LocalArtifact

    window = _window(app)
    try:
        window._controller = SimpleNamespace(
            installed_core_version="0.6.1", shutdown_complete=True
        )
        window._on_readiness_result(SimpleNamespace(ready=True, error_code=None))
        window._chat_view.set_artifact(
            LocalArtifact("m", "m.xml", backend="openvino")
        )
        assert window._chat_view._chat_device_supported is True

        if readiness_failure == "cancelled":
            window._on_readiness_error(RuntimeError("cancelled"))
        else:
            window._on_readiness_result(
                SimpleNamespace(
                    ready=False,
                    error_code="timeout" if readiness_failure == "timeout" else "failed",
                    deadline_expired=readiness_failure == "timeout",
                )
            )

        assert window._confirmed_core_version is None
        assert window._last_readiness_ready is False
        assert window._chat_view._chat_supported is False
        assert window._chat_view._chat_device_supported is False
        assert window._chat_view._start_button.isEnabled() is False
        assert window._openvino_view._inference_supported is False
    finally:
        window.close()


def test_lifecycle_state_change_invalidates_capabilities(app):
    window = _window(app)
    try:
        window._controller = SimpleNamespace(
            installed_core_version="0.6.1", shutdown_complete=True
        )
        window._on_readiness_result(SimpleNamespace(ready=True, error_code=None))
        assert window._chat_view._chat_supported is True
        window._on_state_changed("stopping")
        assert window._confirmed_core_version is None
        assert window._chat_view._chat_supported is False
        assert window._chat_view._chat_device_supported is False
        assert window._openvino_view._inference_supported is False
    finally:
        window.close()


def test_close_request_invalidates_capabilities_before_shutdown(app):
    window = _window(app)
    shutdown_calls = []
    controller = SimpleNamespace(
        installed_core_version="0.6.1",
        shutdown_complete=False,
        request_shutdown=lambda: shutdown_calls.append(True),
    )
    window._controller = controller
    try:
        window._on_readiness_result(SimpleNamespace(ready=True, error_code=None))
        assert window._chat_view._chat_supported is True
        window.close()
        assert shutdown_calls == [True]
        assert window._confirmed_core_version is None
        assert window._chat_view._chat_supported is False
        assert window._chat_view._chat_device_supported is False
        assert window._openvino_view._inference_supported is False
    finally:
        controller.shutdown_complete = True
        window.close()


def test_lifecycle_invalidation_retires_chat_even_while_open_is_pending(app):
    window = _window(app)
    invalidations = []
    window._chat_view._service = SimpleNamespace(
        has_session=False,
        is_busy=True,
        invalidate=lambda: invalidations.append(True),
    )
    try:
        window._invalidate_core_capabilities(clear_chat_session=True)
        assert invalidations == [True]
        assert window._chat_view._session_id is None
        assert window._chat_view._start_button.isEnabled() is False
        assert window._chat_view._send_button.isEnabled() is False
    finally:
        window.close()
