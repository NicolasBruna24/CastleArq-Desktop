# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import gc
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QEventLoop, QThread, QTimer
from PySide6.QtWidgets import QApplication

from castlearq_desktop.core import installation_coordinator as module
from castlearq_desktop.core.installation_coordinator import (
    InstallationCoordinator,
    InstallationState,
)
from castlearq_desktop.core.managed_environment_installer import (
    InstallResult,
    InstallStatus,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _success() -> InstallResult:
    return InstallResult(InstallStatus.SUCCESS, Path("/tmp/x"), None, "0.6.0")


def _finish_thread(qapp, coordinator: InstallationCoordinator) -> None:
    thread = coordinator._thread
    assert thread is not None
    assert thread.wait(5000), "installation worker did not finish"
    qapp.processEvents()


def test_initial_idle(qapp):
    coordinator = InstallationCoordinator()
    assert coordinator.state() == InstallationState.IDLE
    assert not coordinator.is_busy()


def test_normal_completion_order_and_thread_affinity(qapp, monkeypatch):
    monkeypatch.setattr(module, "install_core_0_6_0", lambda **_kwargs: _success())
    coordinator = InstallationCoordinator()
    events = []
    coordinator.state_changed.connect(
        lambda state: events.append(("state", state, QThread.currentThread()))
    )
    coordinator.finished.connect(
        lambda result: events.append(("finished", result, QThread.currentThread()))
    )

    assert coordinator.start()
    _finish_thread(qapp, coordinator)

    assert coordinator.state() == InstallationState.INSTALLED
    assert coordinator.result().status == InstallStatus.SUCCESS
    assert [event[:2] for event in events] == [
        ("state", InstallationState.INSTALLING),
        ("state", InstallationState.INSTALLED),
        ("finished", coordinator.result()),
    ]
    assert all(event[2] == qapp.thread() for event in events)


def test_terminal_signal_callbacks_cannot_reenter_before_completion(qapp, monkeypatch):
    monkeypatch.setattr(module, "install_core_0_6_0", lambda **_kwargs: _success())
    coordinator = InstallationCoordinator()
    reentrant_attempts = []
    coordinator.state_changed.connect(
        lambda state: reentrant_attempts.append(coordinator.start())
        if state == InstallationState.INSTALLED
        else None
    )

    assert coordinator.start()
    _finish_thread(qapp, coordinator)
    assert reentrant_attempts == [False]
    assert coordinator.start()
    _finish_thread(qapp, coordinator)
    assert coordinator.state() == InstallationState.INSTALLED


def test_cancel_request_is_distinct_from_completion_and_is_idempotent(qapp, monkeypatch):
    started = Event()
    release = Event()
    monkeypatch.setattr(
        module,
        "install_core_0_6_0",
        lambda **_kwargs: (started.set(), release.wait(5), _success())[-1],
    )
    coordinator = InstallationCoordinator()
    events = []
    coordinator.cancellation_requested.connect(lambda: events.append("requested"))
    coordinator.cancelled.connect(lambda: events.append("completed"))

    assert coordinator.start()
    assert started.wait(5)
    coordinator.cancel()
    coordinator.cancel()
    assert coordinator.state() == InstallationState.CANCELLING
    assert coordinator.is_busy()
    assert events == ["requested"]

    release.set()
    _finish_thread(qapp, coordinator)
    assert coordinator.state() == InstallationState.CANCELLED
    assert not coordinator.is_busy()
    assert events == ["requested", "completed"]
    assert coordinator.result() is None


def test_cancelled_worker_can_be_restarted_after_completion(qapp, monkeypatch):
    started = Event()
    release = Event()
    calls = 0

    def controlled_install(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            assert release.wait(5)
        return _success()

    monkeypatch.setattr(module, "install_core_0_6_0", controlled_install)
    coordinator = InstallationCoordinator()
    assert coordinator.start()
    assert started.wait(5)
    coordinator.cancel()
    assert coordinator.start() is False
    release.set()
    _finish_thread(qapp, coordinator)

    assert coordinator.state() == InstallationState.CANCELLED
    assert coordinator.start()
    _finish_thread(qapp, coordinator)
    assert coordinator.state() == InstallationState.INSTALLED


def test_duplicate_start_is_rejected_while_installing(qapp, monkeypatch):
    started = Event()
    release = Event()

    def controlled_install(**_kwargs):
        started.set()
        assert release.wait(5)
        return _success()

    monkeypatch.setattr(module, "install_core_0_6_0", controlled_install)
    coordinator = InstallationCoordinator()
    assert coordinator.start()
    assert started.wait(5)
    assert coordinator.start() is False
    release.set()
    _finish_thread(qapp, coordinator)
    assert coordinator.state() == InstallationState.INSTALLED


def test_worker_finishing_before_cancel_wins_and_old_queued_finish_is_ignored(
    qapp, monkeypatch
):
    started_second = Event()
    release_second = Event()
    calls = 0

    def controlled_install(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            started_second.set()
            assert release_second.wait(5)
        return _success()

    monkeypatch.setattr(module, "install_core_0_6_0", controlled_install)
    coordinator = InstallationCoordinator()
    cancelled = []
    coordinator.cancelled.connect(lambda: cancelled.append(True))

    assert coordinator.start()
    old_thread = coordinator._thread
    assert old_thread is not None and old_thread.wait(5000)
    # The real finished signal is queued but not dispatched yet. Since Qt has
    # fully stopped the worker, completion wins over this late cancel request.
    coordinator.cancel()
    assert coordinator.state() == InstallationState.INSTALLED
    assert cancelled == []

    assert coordinator.start()
    new_thread = coordinator._thread
    assert new_thread is not old_thread
    assert started_second.wait(5)
    qapp.processEvents()  # dispatches the old, naturally queued finished signal
    assert coordinator._thread is new_thread
    assert coordinator.state() == InstallationState.INSTALLING

    release_second.set()
    _finish_thread(qapp, coordinator)
    assert coordinator.state() == InstallationState.INSTALLED


def test_worker_finishing_during_cancel_produces_one_coherent_terminal_state(
    qapp, monkeypatch
):
    started = Event()
    release = Event()
    worker_returning = Event()
    events = []

    def controlled_install(**_kwargs):
        started.set()
        assert release.wait(5)
        worker_returning.set()
        return _success()

    monkeypatch.setattr(module, "install_core_0_6_0", controlled_install)
    coordinator = InstallationCoordinator()
    coordinator.cancellation_requested.connect(lambda: events.append("requested"))
    coordinator.cancelled.connect(lambda: events.append("completed"))
    assert coordinator.start()
    assert started.wait(5)

    original_request = module._CancelToken.request_cancellation

    def release_worker_during_request(token):
        original_request(token)
        release.set()
        assert worker_returning.wait(5)
        assert coordinator._thread.wait(5000)

    monkeypatch.setattr(
        module._CancelToken,
        "request_cancellation",
        release_worker_during_request,
    )
    coordinator.cancel()
    assert coordinator.state() == InstallationState.CANCELLING
    qapp.processEvents()
    assert coordinator.state() == InstallationState.CANCELLED
    assert events == ["requested", "completed"]


def test_worker_exception_completes_safely_and_allows_restart(qapp, monkeypatch):
    calls = 0

    def fail_once(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("installer failed")
        return _success()

    monkeypatch.setattr(module, "install_core_0_6_0", fail_once)
    coordinator = InstallationCoordinator()
    errors = []
    coordinator.error.connect(errors.append)
    assert coordinator.start()
    _finish_thread(qapp, coordinator)
    assert coordinator.state() == InstallationState.FAILED
    assert errors == ["installer failed"]
    assert coordinator.start()
    _finish_thread(qapp, coordinator)
    assert coordinator.state() == InstallationState.INSTALLED


def test_cancelled_installer_result_is_not_published_as_success(qapp, monkeypatch):
    monkeypatch.setattr(
        module,
        "install_core_0_6_0",
        lambda **_kwargs: InstallResult(InstallStatus.CANCELLED, None, None, None),
    )
    coordinator = InstallationCoordinator()
    results = []
    completed = []
    coordinator.finished.connect(results.append)
    coordinator.cancelled.connect(lambda: completed.append(True))
    assert coordinator.start()
    _finish_thread(qapp, coordinator)
    assert coordinator.state() == InstallationState.CANCELLED
    assert results == [coordinator.result()]
    assert completed == [True]


def test_coordinator_destruction_keeps_active_thread_until_it_stops(qapp, monkeypatch):
    started = Event()
    release = Event()
    thread_destroyed = Event()

    def controlled_install(**_kwargs):
        started.set()
        assert release.wait(5)
        return _success()

    monkeypatch.setattr(module, "install_core_0_6_0", controlled_install)
    coordinator = InstallationCoordinator()
    assert coordinator.start()
    assert coordinator._thread is not None
    coordinator._thread.destroyed.connect(lambda *_args: thread_destroyed.set())
    assert started.wait(5)

    del coordinator
    gc.collect()
    qapp.processEvents()
    assert not thread_destroyed.is_set()
    assert len(module._thread_registry._threads) == 1

    release.set()
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(5)
    timed_out = Event()
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.setInterval(5000)
    poll.timeout.connect(
        lambda: loop.quit() if not module._thread_registry._threads else None
    )
    watchdog.timeout.connect(lambda: (timed_out.set(), loop.quit()))
    poll.start()
    watchdog.start()
    if module._thread_registry._threads:
        loop.exec()
    poll.stop()
    watchdog.stop()
    gc.collect()
    qapp.processEvents()

    assert not timed_out.is_set()
    assert not module._thread_registry._threads
    assert thread_destroyed.is_set()


def test_pool_argument_is_explicitly_deprecated(qapp, monkeypatch):
    started = Event()
    pool_calls = []

    class LegacyPool:
        def start(self, _runnable):
            pool_calls.append("start")

    monkeypatch.setattr(
        module,
        "install_core_0_6_0",
        lambda **_kwargs: (started.set(), _success())[-1],
    )
    with pytest.warns(FutureWarning, match="pool=.*deprecated"):
        coordinator = InstallationCoordinator(pool=LegacyPool())
    assert coordinator.start()
    assert started.wait(5)
    _finish_thread(qapp, coordinator)
    assert coordinator.state() == InstallationState.INSTALLED
    assert pool_calls == []
