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

"""WP1 tests: explicit interpreter selection in the main window.

Uses an injected fake-backed settings store and a monkeypatched file dialog on
the offscreen Qt platform. No real QSettings is mutated, no interpreter is
executed, and CastleArq Core is never imported or launched.
"""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from castlearq_desktop.core.contracts.settings import (  # noqa: E402
    PYTHON_INTERPRETER_PATH_KEY,
)
from castlearq_desktop.core.interpreter_settings import (  # noqa: E402
    InterpreterSettingsStore,
)
from castlearq_desktop.ui.main_window import MainWindow  # noqa: E402


class FakeSettings:
    def __init__(self) -> None:
        self.data: dict = {}

    def value(self, key, defaultValue=None):
        return self.data.get(key, defaultValue)

    def setValue(self, key, value) -> None:
        self.data[key] = value

    def remove(self, key) -> None:
        self.data.pop(key, None)

    def sync(self) -> None:
        pass


@pytest.fixture()
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def fake_settings() -> FakeSettings:
    return FakeSettings()


@pytest.fixture()
def window(app, fake_settings) -> MainWindow:
    instance = MainWindow(interpreter_settings=InterpreterSettingsStore(fake_settings))
    yield instance
    instance.close()


def _patch_dialog(monkeypatch, path: str) -> None:
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (path, "")),
    )


def test_cancelled_selection_persists_nothing_and_starts_no_core(
    window, fake_settings, monkeypatch
) -> None:
    _patch_dialog(monkeypatch, "")
    window._on_select_interpreter()
    assert fake_settings.data == {}
    assert "castlearq" not in sys.modules
    assert "castlearq.main" not in sys.modules


def test_absolute_selection_is_persisted_under_the_approved_key(
    window, fake_settings, monkeypatch
) -> None:
    _patch_dialog(monkeypatch, "/abs/env/bin/python")
    window._on_select_interpreter()
    assert fake_settings.data == {PYTHON_INTERPRETER_PATH_KEY: "/abs/env/bin/python"}
    assert window._interpreter_label.text() == "/abs/env/bin/python"


def test_relative_selection_is_rejected_before_persistence(
    window, fake_settings, monkeypatch
) -> None:
    _patch_dialog(monkeypatch, "python3")
    window._on_select_interpreter()
    assert fake_settings.data == {}


def test_cancel_keeps_a_previously_saved_selection(window, fake_settings, monkeypatch) -> None:
    _patch_dialog(monkeypatch, "/abs/env/bin/python")
    window._on_select_interpreter()
    _patch_dialog(monkeypatch, "")
    window._on_select_interpreter()
    assert fake_settings.data == {PYTHON_INTERPRETER_PATH_KEY: "/abs/env/bin/python"}


def test_window_never_imports_or_launches_core(window) -> None:
    assert "castlearq" not in sys.modules
    assert "castlearq.main" not in sys.modules


def test_updates_import_failure_uses_fallback(app, fake_settings, monkeypatch) -> None:
    """ImportError on the optional Updates module keeps the planned fallback."""
    import sys

    import castlearq_desktop.ui.main_window as main_window_mod

    monkeypatch.setitem(sys.modules, "castlearq_desktop.ui.updates_section", None)
    # Ensure the stale binding is not reused: the import runs inside __init__.
    window = main_window_mod.MainWindow(
        interpreter_settings=InterpreterSettingsStore(fake_settings))
    try:
        assert window._update_controller is None
        assert window._updates_section.text() == "Updates unavailable."
    finally:
        window.close()


def test_updates_construction_error_propagates(app, fake_settings, monkeypatch) -> None:
    """A build-time error is not swallowed by the import fallback."""
    import castlearq_desktop.ui.main_window as main_window_mod
    import castlearq_desktop.ui.updates_section as updates_mod

    def _broken(*args, **kwargs):
        raise RuntimeError("construction blew up")

    monkeypatch.setattr(updates_mod, "UpdatesSection", _broken)
    with pytest.raises(RuntimeError, match="construction blew up"):
        main_window_mod.MainWindow(
            interpreter_settings=InterpreterSettingsStore(fake_settings))
