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

"""WP1 tests: interpreter path configuration.

Unit tests inject an in-memory fake settings object; no real QSettings is ever
mutated, no PATH lookup occurs, and no interpreter is executed. One factory
test additionally constructs a real ``QSettings`` (read-only) to prove the
approved identity applies without writing, removing, or syncing any setting.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from castlearq_desktop.core.contracts.settings import (
    APPLICATION_NAME,
    FORBIDDEN_SETTING_KEYS,
    ORGANIZATION_DOMAIN,
    ORGANIZATION_NAME,
    PYTHON_INTERPRETER_PATH_KEY,
    qsettings_identity,
)
from castlearq_desktop.core.interpreter_settings import (
    InterpreterSettingsStore,
    create_interpreter_settings,
)


class FakeSettings:
    """Minimal QSettings-shaped in-memory double."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.sync_calls = 0

    def value(self, key, defaultValue=None):
        return self.data.get(key, defaultValue)

    def setValue(self, key, value) -> None:
        self.data[key] = value

    def remove(self, key) -> None:
        self.data.pop(key, None)

    def sync(self) -> None:
        self.sync_calls += 1


@pytest.fixture()
def fake() -> FakeSettings:
    return FakeSettings()


@pytest.fixture()
def store(fake: FakeSettings) -> InterpreterSettingsStore:
    return InterpreterSettingsStore(fake)


# --- Identity and key discipline (AC1, AC12) -------------------------------


def test_store_touches_only_the_approved_key(store, fake) -> None:
    store.save("/abs/bin/python")
    assert set(fake.data) == {PYTHON_INTERPRETER_PATH_KEY}
    assert PYTHON_INTERPRETER_PATH_KEY == "runtime/python_interpreter_path"


def test_identity_constants_match_the_approved_triple() -> None:
    assert qsettings_identity() == {
        "organization": "CastleArq",
        "organization_domain": "castlearq.local",
        "application": "CastleArq Desktop",
    }
    assert (ORGANIZATION_NAME, ORGANIZATION_DOMAIN, APPLICATION_NAME) == (
        "CastleArq",
        "castlearq.local",
        "CastleArq Desktop",
    )


def test_no_validation_or_process_results_are_persisted(store, fake) -> None:
    store.save("/abs/bin/python")
    assert not (set(fake.data) & FORBIDDEN_SETTING_KEYS)
    assert "runtime/validation_result" not in fake.data
    assert "runtime/core_pid" not in fake.data


# --- Load / save / replace / clear (AC2) -----------------------------------


def test_missing_selection_is_unconfigured(store) -> None:
    assert store.load() is None


def test_save_then_load_round_trip(store) -> None:
    store.save("/abs/bin/python3.12")
    assert store.load() == "/abs/bin/python3.12"


def test_explicit_replacement_overwrites_the_selection(store) -> None:
    store.save("/abs/bin/python3.11")
    store.save("/abs/venv/bin/python")
    assert store.load() == "/abs/venv/bin/python"


def test_clear_returns_to_unconfigured(store, fake) -> None:
    store.save("/abs/bin/python")
    store.clear()
    assert store.load() is None
    assert PYTHON_INTERPRETER_PATH_KEY not in fake.data


def test_empty_stored_value_reads_as_unconfigured(store, fake) -> None:
    fake.data[PYTHON_INTERPRETER_PATH_KEY] = ""
    assert store.load() is None


# --- Absolute-path enforcement and no discovery (AC2) ----------------------


@pytest.mark.parametrize("path", ["python3", "./python", "bin/python", ""])
def test_relative_or_empty_paths_are_rejected_before_any_write(store, fake, path) -> None:
    with pytest.raises(ValueError):
        store.save(path)
    assert fake.data == {}


def test_load_performs_no_fallback_discovery(store) -> None:
    """Missing key → None; nothing is searched in PATH or the environment."""
    assert store.load() is None


def test_load_returns_stored_value_verbatim_without_correction(store) -> None:
    """Whatever was stored is returned; the store never infers or upgrades."""
    store.save("/odd/location/python")
    assert store.load() == "/odd/location/python"


def test_modules_perform_no_path_or_environment_discovery() -> None:
    """Neither configuration nor validation modules may discover interpreters."""
    import castlearq_desktop.core.interpreter_settings as settings_module
    import castlearq_desktop.core.interpreter_validator as validator_module

    for module in (settings_module, validator_module):
        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        assert "shutil" not in source
        assert "which(" not in source
        assert "os.environ" not in source


# --- Factory uses the approved identity (AC1) ------------------------------


def test_factory_builds_qsettings_with_the_approved_identity(monkeypatch) -> None:
    calls: dict = {}

    class FakeQSettings:
        """Mirrors the real PySide6 6.12 QSettings surface: it has no domain API."""

        def __init__(self, organization, application=None):
            calls["constructor"] = (organization, application)

        def value(self, key, defaultValue=None):
            return defaultValue

        def setValue(self, key, value) -> None:
            raise AssertionError("the factory must not write settings")

        def remove(self, key) -> None:
            pass

        def sync(self) -> None:
            pass

    class FakeCoreApplication:
        """Qt's only supported home for the organization-domain identity."""

        @staticmethod
        def setOrganizationDomain(domain) -> None:
            calls["domain"] = domain

    fake_qtcore = types.ModuleType("PySide6.QtCore")
    fake_qtcore.QSettings = FakeQSettings
    fake_qtcore.QCoreApplication = FakeCoreApplication
    fake_pyside = types.ModuleType("PySide6")
    fake_pyside.QtCore = fake_qtcore
    monkeypatch.setitem(sys.modules, "PySide6", fake_pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", fake_qtcore)

    store = create_interpreter_settings()

    assert isinstance(store, InterpreterSettingsStore)
    assert calls["constructor"] == (ORGANIZATION_NAME, APPLICATION_NAME)
    assert calls["domain"] == ORGANIZATION_DOMAIN


def test_factory_constructs_real_qsettings_with_identity_and_no_writes(
    monkeypatch,
) -> None:
    """F-1: the real PySide6 QSettings factory must construct without error.

    Verifies the identity behavior the actual Qt API supports (organization
    name and application name on ``QSettings``, organization domain on
    ``QCoreApplication``) and proves that factory construction performs no
    write, removal, or sync on the real settings backend: the resolved
    settings-file state is byte- and mtime-identical before and after.
    """
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QCoreApplication, QSettings

    # Construct-only probe of the exact location the factory will resolve to.
    sentinel = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)
    settings_path = Path(sentinel.fileName())
    existed_before = settings_path.exists()
    content_before = settings_path.read_bytes() if existed_before else None
    mtime_before = settings_path.stat().st_mtime_ns if existed_before else None
    del sentinel  # an unmodified QSettings destructor syncs nothing

    store = create_interpreter_settings()

    settings = store._settings
    assert isinstance(settings, QSettings)
    assert settings.organizationName() == ORGANIZATION_NAME
    assert settings.applicationName() == APPLICATION_NAME
    assert QCoreApplication.organizationDomain() == ORGANIZATION_DOMAIN

    # No real setting was created, modified, or removed by the factory.
    assert Path(settings.fileName()) == settings_path
    if existed_before:
        assert settings_path.exists()
        assert settings_path.read_bytes() == content_before
        assert settings_path.stat().st_mtime_ns == mtime_before
    else:
        assert not settings_path.exists()
