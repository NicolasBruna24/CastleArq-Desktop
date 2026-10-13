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

"""Minimal structural and boundary tests for CastleArq Desktop.

These tests verify the Phase 1 application skeleton:

* the package and its modules import cleanly;
* the non-GUI boundaries exist and perform no real work (no HTTP requests, no
  process management, no database persistence);
* the Qt application shell can be constructed when PySide6 is available.

The non-GUI tests deliberately avoid importing PySide6 so they can run on a
machine where the GUI dependency is not installed. The GUI smoke test is
skipped automatically in that case.
"""

from __future__ import annotations

import importlib

import pytest

from castlearq_desktop import APP_NAME, ORGANIZATION_DOMAIN, ORGANIZATION_NAME
from castlearq_desktop.core import CoreHttpClient, CoreProcessManager, HttpClientError
from castlearq_desktop.storage import ConversationStore

EXPECTED_NON_GUI_MODULES = [
    "castlearq_desktop",
    "castlearq_desktop.core",
    "castlearq_desktop.core.http_client",
    "castlearq_desktop.core.process_manager",
    "castlearq_desktop.storage",
    "castlearq_desktop.storage.conversation_store",
]

# GUI modules import PySide6 at load time and are only exercised when the GUI
# dependency is installed.
EXPECTED_GUI_MODULES = [
    "castlearq_desktop.app",
    "castlearq_desktop.ui",
    "castlearq_desktop.ui.main_window",
]


@pytest.mark.parametrize("module_name", EXPECTED_NON_GUI_MODULES)
def test_non_gui_module_imports(module_name: str) -> None:
    """Each non-GUI skeleton module must import without PySide6 installed."""
    assert importlib.import_module(module_name) is not None


@pytest.mark.parametrize("module_name", EXPECTED_GUI_MODULES)
def test_gui_module_imports(module_name: str) -> None:
    """Each GUI module must import when PySide6 is available (skipped otherwise)."""
    pytest.importorskip("PySide6")
    assert importlib.import_module(module_name) is not None


def test_app_metadata_constants() -> None:
    """Application identity constants must be defined and non-empty."""
    assert APP_NAME == "CastleArq Desktop"
    assert ORGANIZATION_NAME
    assert ORGANIZATION_DOMAIN


def test_http_client_boundary_makes_no_requests() -> None:
    """The HTTP adapter must expose a boundary but perform no requests."""
    client = CoreHttpClient(base_url="http://127.0.0.1:9", timeout=0.1)
    assert client.base_url == "http://127.0.0.1:9"
    with pytest.raises(NotImplementedError):
        client.get("/health")
    with pytest.raises(NotImplementedError):
        client.post("/chat", json={})
    assert issubclass(HttpClientError, Exception)


def test_process_manager_boundary_starts_no_processes() -> None:
    """The process manager must be a boundary that manages no process."""
    manager = CoreProcessManager(executable="/nonexistent/core")
    assert manager.executable == "/nonexistent/core"
    assert manager.is_running() is False
    with pytest.raises(NotImplementedError):
        manager.start()
    with pytest.raises(NotImplementedError):
        manager.stop()


def test_conversation_store_boundary_persists_nothing() -> None:
    """The storage boundary must not read or write conversations."""
    store = ConversationStore(database_path="/nonexistent/dir/db.sqlite3")
    assert store.database_path == "/nonexistent/dir/db.sqlite3"
    with pytest.raises(NotImplementedError):
        store.save_conversation("conv-1", [])
    with pytest.raises(NotImplementedError):
        store.load_conversation("conv-1")


def test_gui_shell_constructs_headless() -> None:
    """Build the Qt application and main window without a display.

    Skipped when PySide6 is not installed. Uses the ``offscreen`` Qt platform
    so no real display or Core process is required.
    """
    pytest.importorskip("PySide6")

    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from castlearq_desktop.app import create_application
    from castlearq_desktop.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)

    window = MainWindow()
    assert window.windowTitle() == APP_NAME

    # The central widget and its placeholder must exist.
    assert window.centralWidget() is not None

    window.show()
    assert window.isVisible() is True
    assert window.windowFlags() & Qt.WindowType.Window

    window.close()

    # create_application must reuse the existing QApplication instance.
    assert create_application([]) is app
