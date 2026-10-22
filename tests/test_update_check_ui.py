# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Stage 3 tests: async controller + Updates UI (no network)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from castlearq_desktop.core.update_check_controller import (  # noqa: E402
    UpdateCheckController,
)
from castlearq_desktop.core.update_discovery import (  # noqa: E402
    CoreUpdateDiscoveryState as S,
)
from castlearq_desktop.core.update_discovery import (  # noqa: E402
    UpdateDiscoveryError,
    UpdateDiscoveryFailureReason,
    UpdateDiscoveryResult,
)
from castlearq_desktop.ui.updates_section import (  # noqa: E402
    UpdatesSection,
    describe_result,
)


class FakePool:
    def __init__(self):
        self.queued = []

    def start(self, runnable):
        self.queued.append(runnable)

    def drain(self):
        queued, self.queued = self.queued, []
        for r in queued:
            r.run()


class FakeSettings:
    def __init__(self, path=None):
        self._path = path

    def load(self):
        return self._path


def _res(state, installed="0.5.1", latest="0.5.1", search=None):
    return UpdateDiscoveryResult(
        state=state, installed_version=installed, latest_stable=latest,
        admitted_versions=frozenset({"0.5.1"}), search_failure=search,
        installed_failure=None, technical_context={},
    )


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


def _ctl(**kw):
    pool = FakePool()
    kw.setdefault("thread_pool", pool)
    return UpdateCheckController(**kw), pool


def test_check_starts_loading(app):
    ctl, pool = _ctl(discover=lambda **k: _res(S.LATEST_INSTALLED))
    events = []
    ctl.check_started.connect(lambda: events.append("started"))
    assert ctl.check() is True
    assert ctl.is_checking and events == ["started"]


def test_success_shows_three_versions(app):
    ctl, pool = _ctl(discover=lambda **k: _res(S.LATEST_INSTALLED))
    got = []
    ctl.check_finished.connect(lambda r: got.append(r))
    ctl.check()
    pool.drain()
    assert got and got[0].installed_version == "0.5.1"
    status, detail = describe_result(got[0])
    assert "0.5.1" in detail and "Admitted" in detail


def test_success_without_interpreter(app):
    seen = {}

    def _discover(**kw):
        seen.update(kw)
        return _res(S.INSTALLED_UNKNOWN, installed=None, latest="0.6.0")

    ctl, pool = _ctl(interpreter_settings=FakeSettings(None),
                     discover=_discover)
    got = []
    ctl.check_finished.connect(lambda r: got.append(r))
    ctl.check()
    assert seen.get("python_path") is None
    pool.drain()
    status, _ = describe_result(got[0])
    assert "unknown" in status.lower()


def test_pypi_failure_then_retry(app):
    calls = {"n": 0}

    def _discover(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _res(S.SEARCH_FAILED, latest=None,
                        search=UpdateDiscoveryError(
                            UpdateDiscoveryFailureReason.NETWORK_ERROR))
        return _res(S.LATEST_INSTALLED)

    ctl, pool = _ctl(discover=_discover)
    got = []
    ctl.check_finished.connect(lambda r: got.append(r))
    ctl.check()
    pool.drain()
    assert got[0].state is S.SEARCH_FAILED
    ctl.check()
    pool.drain()
    assert got[1].state is S.LATEST_INSTALLED


def test_double_trigger_single_flight(app):
    ctl, pool = _ctl(discover=lambda **k: _res(S.LATEST_INSTALLED))
    dup = []
    ctl.already_running.connect(lambda: dup.append(True))
    assert ctl.check() is True
    assert ctl.check() is False
    assert dup == [True] and len(pool.queued) == 1


def test_stale_result_dropped(app):
    ctl, pool = _ctl(discover=lambda **k: _res(S.LATEST_INSTALLED))
    got = []
    ctl.check_finished.connect(lambda r: got.append(r))
    ctl.check()
    ctl.invalidate()
    pool.drain()
    assert got == [] and not ctl.is_checking


def test_close_during_check(app):
    ctl, pool = _ctl(discover=lambda **k: _res(S.LATEST_INSTALLED))
    sec = UpdatesSection(controller=ctl)
    finished = []
    ctl.check_finished.connect(lambda r: finished.append(r))
    ctl.check()
    assert not sec._button.isEnabled()
    sec.close_section()
    pool.drain()  # late worker outcome must be dropped, UI untouched
    assert finished == []
    assert not sec._button.isEnabled()
    assert "Checking" in sec._status.text()
    sec.close()


def test_delivery_on_qt_thread(app):
    import threading
    import time
    from PySide6.QtCore import QThread
    main_thread = app.thread()
    seen = {}
    slot_threads = []

    def _discover(**kw):
        seen["worker"] = QThread.currentThread()
        return _res(S.LATEST_INSTALLED)

    ctl, pool = _ctl(discover=_discover)
    got = []

    def _slot(result):
        got.append(result)
        slot_threads.append(QThread.currentThread())

    ctl.check_finished.connect(_slot)
    ctl.check()
    worker = threading.Thread(target=pool.drain)
    worker.start()
    worker.join(5)
    deadline = time.monotonic() + 5
    while not got and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert got, "queued signal was never delivered"
    assert seen["worker"] is not main_thread
    assert slot_threads and slot_threads[0] is main_thread


def test_not_admitted_copy(app):
    r = _res(S.NEWER_STABLE_NOT_ADMITTED, installed="0.5.1", latest="0.6.0")
    status, _ = describe_result(r)
    assert "NOT admitted" in status
    assert "Update" not in status


def test_controller_neutrality(app):
    import castlearq_desktop.core.contracts.errors as errors_mod
    from castlearq_desktop.core import version_admission as va_mod
    before = frozenset(errors_mod.ErrorCode)
    ctl, pool = _ctl(discover=lambda **k: _res(S.LATEST_INSTALLED))
    ctl.check()
    pool.drain()
    assert va_mod.SUPPORTED_CORE_VERSIONS == frozenset({"0.5.1", "0.6.0"})
    assert frozenset(errors_mod.ErrorCode) == before
