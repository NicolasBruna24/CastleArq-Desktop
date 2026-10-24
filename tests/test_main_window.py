# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for MainWindow UI navigation structure."""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
        assert "sin información registrada" in window._runtime_lifecycle_label.text()
        assert "sin información registrada" in window._runtime_shutdown_label.text()
        assert "sin información registrada" in window._runtime_shutdown_complete_label.text()
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
        assert "sin información registrada" in window._runtime_lifecycle_label.text()
        assert "sin información registrada" in window._runtime_shutdown_label.text()
        assert "sin información registrada" in window._runtime_pid_label.text()
        assert "sin información registrada" in window._runtime_exit_code_label.text()
        assert "sin información registrada" in window._runtime_shutdown_complete_label.text()
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
