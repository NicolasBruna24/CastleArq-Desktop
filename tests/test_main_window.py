# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for MainWindow UI integration and library refresh scheduling."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

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


def test_qscrollarea_structure(app):
    window = _window(app)
    try:
        scroll = window.centralWidget()
        assert isinstance(scroll, QScrollArea)
        assert scroll.widgetResizable() is True
        inner = scroll.widget()
        assert isinstance(inner, QWidget)
        layout = inner.layout()
        assert isinstance(layout, QVBoxLayout)
    finally:
        window.close()


def test_schedule_refresh_service_free(app):
    window = _window(app)
    try:
        service = MagicMock()
        service.is_busy = False
        service.refresh.return_value = True
        window._schedule_library_refresh(service)
        service.refresh.assert_called_once()
        assert window._pending_library_refresh is False
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
