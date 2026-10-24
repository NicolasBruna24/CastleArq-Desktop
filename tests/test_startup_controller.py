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

"""Focused integration tests for the application-level ``StartupController``.

These tests exercise the controller together with the real ``CoreStartupCoordinator``,
``LifecycleManager``/``LifecycleObserver`` and the Qt readiness transport, using
deterministic fakes for the process, network, clock, timers, and settings. No real
CastleArq Core process is launched, no user settings are mutated, and no network
or external service is contacted.

Two independent fake-timer registries keep the coordinator's timers (lifecycle
observation + readiness) separate from the controller's timers (UI state tick +
application-level shutdown deadline), so each can be driven deterministically.
"""

from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace
from typing import Optional

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from castlearq_desktop.app import shutdown_exit_code  # noqa: E402
from castlearq_desktop.core.contracts import ErrorCode, VersionAdmission  # noqa: E402
from castlearq_desktop.core.contracts.interpreter import (  # noqa: E402
    ValidationOutcome,
)
from castlearq_desktop.core.interpreter_settings import (  # noqa: E402
    InterpreterSettingsStore,
)
from castlearq_desktop.core.lifecycle_manager import (  # noqa: E402
    LifecycleState,
    OperationResult,
)
from castlearq_desktop.core.startup_controller import (  # noqa: E402
    MonotonicClock,
    StartupController,
)
from castlearq_desktop.core.startup_coordinator import (  # noqa: E402
    CoreStartupCoordinator,
)
from castlearq_desktop.ui.main_window import MainWindow  # noqa: E402

ABS_PYTHON = "/abs/env/bin/python"

#: Test-only admitted version used by the Qt readiness doubles (not production).
TEST_VERSION = "9.9.9-test"


def _health_body(version: str = TEST_VERSION) -> bytes:
    """Well-formed /health body whose top-level ``version`` is *version*."""
    import json
    return json.dumps({"version": version}).encode()


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


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


class FakeSignal:
    def __init__(self) -> None:
        self.connections: list = []

    def connect(self, slot) -> None:
        self.connections.append(slot)

    def emit(self, *args) -> None:
        for slot in list(self.connections):
            slot(*args)


class FakeTimer:
    def __init__(self, registry: list) -> None:
        self.timeout = FakeSignal()
        self.interval = None
        self._single = False
        self._active = False
        self._registry = registry

    def setInterval(self, ms) -> None:
        self.interval = ms

    def setSingleShot(self, single) -> None:
        self._single = single

    def isActive(self) -> bool:
        return self._active

    def start(self) -> None:
        self._active = True
        self._registry.append(self)

    def stop(self) -> None:
        self._active = False

    def fire(self) -> None:
        self.timeout.emit()


class FakePopen:
    def __init__(self, exit_code=None, raises=None) -> None:
        self._exit_code = exit_code
        self._raises = raises or {}
        self.terminates = 0
        self.kills = 0

    def poll(self):
        action = self._raises.get("poll")
        if action is not None:
            raise action
        return self._exit_code

    def is_alive(self) -> bool:
        return self._exit_code is None

    def terminate(self) -> None:
        self.terminates += 1

    def kill(self) -> None:
        self.kills += 1

    def exit(self, code=0) -> None:
        self._exit_code = code


class PopenFactory:
    def __init__(self, popen) -> None:
        self.popen = popen
        self.calls: list = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.popen

class QtFakeReply:
    def __init__(self) -> None:
        self.finished = FakeSignal()
        self._error = QNetworkReply.NetworkError.NoError
        self._status = None
        self.aborted = False

    def error(self):
        return self._error

    def attribute(self, attr):
        if attr == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self._status
        return None

    def abort(self) -> None:
        self.aborted = True
        self._error = QNetworkReply.NetworkError.OperationCanceledError
        self.finished.emit()

    def deleteLater(self) -> None:
        pass

    def succeed(self, status) -> None:
        self._error = QNetworkReply.NetworkError.NoError
        self._status = status
        self.finished.emit()

    def http_error(self, status) -> None:
        self._error = QNetworkReply.NetworkError.ProtocolFailure
        self._status = status
        self.finished.emit()

    def readAll(self):
        # DEC-A: the Qt transport reads the /health body via readAll() to
        # extract the top-level "version". Returning a well-formed body lets
        # admission be evaluated for real instead of short-circuiting to
        # MISSING on an absent body. Only tests that also inject an
        # installed-version provider consult it; tests that rely on the
        # static ``api_contract`` input (e.g. the fail-closed case) keep
        # that input untouched, since no provider is configured for them.
        return _health_body()


class QtFakeNAM:
    def __init__(self) -> None:
        self.requests: list = []

    def get(self, request):
        reply = QtFakeReply()
        self.requests.append((request.url().toString(), reply))
        return reply


def valid_outcome(python=ABS_PYTHON) -> ValidationOutcome:
    return ValidationOutcome(code=None, python_version=(3, 12, 0),
                             distribution_found=True, package_found=True,
                             detail="ok")


@pytest.fixture()
def qt_app():
    return QApplication.instance() or QApplication(sys.argv[:1])


def build(popen, *, api_contract=VersionAdmission.ADMITTED,
          admitted_version: Optional[str] = TEST_VERSION):
    """Construct a coordinator + controller wired with two timer registries.

    When *admitted_version* is given, DEC-A is driven for real: an
    installed-version provider plus an explicit test-only allowlist are
    injected, and the Qt double's /health body reports that same version, so
    admission is genuinely evaluated rather than assumed. Pass
    ``admitted_version=None`` to leave the static ``api_contract`` input in
    force (used by the fail-closed test, which asserts readiness never
    succeeds because admission was not evaluated).
    """
    settings = InterpreterSettingsStore(FakeSettings())
    settings.save(ABS_PYTHON)
    clock = FakeClock()
    coord_reg: list = []
    ctrl_reg: list = []
    nam = QtFakeNAM()
    admission_kwargs = {}
    if admitted_version is not None:
        admission_kwargs["installed_version_provider"] = lambda: admitted_version
        admission_kwargs["supported_versions"] = frozenset({admitted_version})
    coordinator = CoreStartupCoordinator(
        settings=settings,
        clock=clock,
        popen_factory=PopenFactory(popen),
        timer_factory=lambda: FakeTimer(coord_reg),
        validator=valid_outcome,
        api_contract=api_contract,
        qt_nam=nam,
        **admission_kwargs,
    )
    controller = StartupController(coordinator,
                                   timer_factory=lambda: FakeTimer(ctrl_reg))
    return SimpleNamespace(controller=controller, coordinator=coordinator,
                           clock=clock, nam=nam, coord_reg=coord_reg,
                           ctrl_reg=ctrl_reg, popen=popen)


def fire(reg, ms):
    for t in list(reg):
        if t.interval == ms:
            t.fire()


# --- startup -----------------------------------------------------------------
def test_duplicate_startup_prevented():
    b = build(FakePopen())
    first = b.controller.start()
    assert first is not None
    assert b.coordinator.spawn_calls == 1
    second = b.controller.start()
    assert second is None  # duplicate concurrent start suppressed
    assert b.coordinator.spawn_calls == 1


def test_state_and_readiness_signal_propagation():
    b = build(FakePopen())  # ADMITTED
    states, results = [], []
    b.controller.state_changed.connect(lambda s: states.append(s))
    b.controller.readiness_result.connect(lambda r: results.append(r))
    b.controller.start()
    assert LifecycleState.RUNNING in states
    assert b.controller._active_checker is not None
    for _, reply in b.nam.requests:
        reply.succeed(200)
    assert len(results) == 1
    assert results[-1].ready is True


def test_checker_retained_for_attempt():
    b = build(FakePopen())
    b.controller.start()
    checker = b.controller._active_checker
    assert checker is not None
    # Still retained while the operation is pending (probes not completed).
    assert b.controller._active_checker is checker
    assert len(b.nam.requests) == 3


def test_stale_readiness_result_suppressed():
    b = build(FakePopen())
    b.controller.start()
    active = b.controller._active_checker
    results = []
    b.controller.readiness_result.connect(lambda r: results.append(r))
    # A result attributed to a checker that is not the active one is ignored.
    b.controller._on_readiness_result(SimpleNamespace(ready=True), object())
    assert results == []
    # A result from the active checker is delivered exactly once.
    for _, reply in b.nam.requests:
        reply.succeed(200)
    assert len(results) == 1 and results[-1].ready is True
    assert b.controller._active_checker is active


def test_fail_closed_when_admission_not_evaluated():
    b = build(FakePopen(), api_contract=VersionAdmission.NOT_EVALUATED,
              admitted_version=None)
    results = []
    b.controller.readiness_result.connect(lambda r: results.append(r))
    b.controller.start()
    assert b.coordinator.manager.state is LifecycleState.RUNNING
    b.clock.advance(20.0)  # reach the global readiness deadline
    for _, reply in b.nam.requests:
        reply.succeed(200)
    # Admission not evaluated -> readiness never succeeds; terminal is a timeout.
    assert results and all(r.ready is False for r in results)


def test_close_while_readiness_pending():
    b = build(FakePopen())  # ADMITTED
    shutdown_states = []
    b.controller.shutdown_finished.connect(lambda s: shutdown_states.append(s))
    b.controller.start()
    assert b.controller._active_checker is not None
    # Readiness still pending; a close request cancels it and starts shutdown.
    b.controller.request_shutdown()
    assert b.controller._active_checker is None
    assert b.coordinator.manager.state is LifecycleState.STOPPING
    assert all(reply.aborted for _, reply in b.nam.requests)
    # Drive shutdown to a confirmed terminal outcome.
    b.popen.exit(0)
    fire(b.coord_reg, 100)  # observer -> observe() -> STOPPED
    fire(b.ctrl_reg, 100)   # tick -> finalize STOPPED
    assert shutdown_states == [LifecycleState.STOPPED]


# --- shutdown ----------------------------------------------------------------
def test_repeated_shutdown_does_not_reset_deadline():
    b = build(FakePopen())
    b.controller.start()
    b.controller.request_shutdown()
    deadline_timer = b.controller._shutdown_deadline_timer
    assert deadline_timer is not None
    assert len([t for t in b.ctrl_reg if t.interval == 5000]) == 1
    # A repeated request is coalesced: same timer instance, no reset.
    b.controller.request_shutdown()
    assert b.controller._shutdown_deadline_timer is deadline_timer
    assert len([t for t in b.ctrl_reg if t.interval == 5000]) == 1


def test_confirmed_shutdown_reports_stopped():
    b = build(FakePopen())
    b.controller.start()
    states = []
    b.controller.shutdown_finished.connect(lambda s: states.append(s))
    b.controller.request_shutdown()
    assert b.coordinator.manager.state is LifecycleState.STOPPING
    b.popen.exit(0)
    fire(b.coord_reg, 100)
    fire(b.ctrl_reg, 100)
    assert states == [LifecycleState.STOPPED]
    assert b.popen.terminates == 1


def test_unconfirmed_shutdown_reports_failed():
    b = build(FakePopen())
    b.controller.start()
    states = []
    b.controller.shutdown_finished.connect(lambda s: states.append(s))
    b.controller.request_shutdown()
    # The process never exits; the application-level 5 s deadline fires.
    fire(b.ctrl_reg, 5000)
    assert states == [LifecycleState.SHUTDOWN_FAILED]
    assert b.popen.terminates == 1


def test_no_termination_after_deadline():
    b = build(FakePopen())
    b.controller.start()
    b.controller.request_shutdown()
    fire(b.ctrl_reg, 5000)  # deadline -> SHUTDOWN_FAILED (terminal)
    assert b.controller.shutdown_complete is True
    assert (b.popen.terminates, b.popen.kills) == (1, 0)
    # No further signals or stop() calls after the terminal outcome.
    fire(b.coord_reg, 100)        # observer fires again -> no escalation
    b.controller.request_shutdown()  # terminal -> no-op
    fire(b.ctrl_reg, 5000)        # deadline fires again -> no-op
    assert (b.popen.terminates, b.popen.kills) == (1, 0)


def test_shutdown_exit_code_mapping():
    assert shutdown_exit_code(LifecycleState.SHUTDOWN_FAILED) == 1
    assert shutdown_exit_code(LifecycleState.STOPPED) == 0


def test_terminal_failure_maps_to_nonzero_exit():
    b = build(FakePopen())
    b.controller.start()
    exit_codes = []
    b.controller.shutdown_finished.connect(
        lambda s: exit_codes.append(shutdown_exit_code(s)))
    b.controller.request_shutdown()
    fire(b.ctrl_reg, 5000)
    assert exit_codes == [1]


def test_event_loop_responsive_during_shutdown(qt_app):
    popen = FakePopen()  # never exits on its own
    settings = InterpreterSettingsStore(FakeSettings())
    settings.save(ABS_PYTHON)
    nam = QtFakeNAM()  # readiness requests never complete
    coordinator = CoreStartupCoordinator(
        settings=settings, clock=MonotonicClock(),
        popen_factory=PopenFactory(popen), validator=valid_outcome,
        api_contract=VersionAdmission.ADMITTED, qt_nam=nam)
    controller = StartupController(coordinator)  # real QTimer
    controller.start()  # readiness pending
    heartbeat = []
    hb = QTimer()
    hb.setInterval(5)
    hb.timeout.connect(lambda: heartbeat.append(1))
    hb.start()
    loop = QEventLoop()
    QTimer.singleShot(60, loop.quit)
    t0 = time.monotonic()
    controller.request_shutdown()  # must return immediately (non-blocking)
    request_ms = (time.monotonic() - t0) * 1000.0
    loop.exec()
    hb.stop()
    assert len(heartbeat) >= 2      # event loop kept running while pending
    assert request_ms < 1000        # shutdown request did not block
    # Drive to a confirmed terminal to release the pending deadline timer.
    popen.exit(0)
    loop2 = QEventLoop()
    QTimer.singleShot(200, loop2.quit)
    loop2.exec()
    assert controller.shutdown_complete is True


def test_main_window_construction_compatibility(qt_app):
    store = InterpreterSettingsStore(FakeSettings())
    # Legacy constructions remain valid and expose no active controller.
    w1 = MainWindow()
    assert w1._start_button.isEnabled() is False
    w1.close()
    w2 = MainWindow(interpreter_settings=store)
    assert w2._start_button.isEnabled() is False
    w2.close()
    # With a controller, the Start control is wired and enabled.
    b = build(FakePopen())
    w3 = MainWindow(interpreter_settings=b.coordinator.settings,
                    controller=b.controller)
    assert w3._start_button.isEnabled() is True
    w3._controller.request_shutdown()  # keep the deferred-close path deterministic


# --- startup failure transparency ---------------------------------------------

def test_start_failure_displays_error_code_and_message(qt_app):
    """An unconfigured interpreter surfaces INTERPRETER_INVALID + message."""
    settings = InterpreterSettingsStore(FakeSettings())  # nothing saved
    coord_reg: list = []
    ctrl_reg: list = []
    coordinator = CoreStartupCoordinator(
        settings=settings,
        clock=FakeClock(),
        popen_factory=PopenFactory(FakePopen()),
        timer_factory=lambda: FakeTimer(coord_reg),
        validator=valid_outcome,
        api_contract=VersionAdmission.ADMITTED,
        qt_nam=QtFakeNAM(),
    )
    controller = StartupController(
        coordinator, timer_factory=lambda: FakeTimer(ctrl_reg))
    window = MainWindow(interpreter_settings=settings, controller=controller)
    try:
        window._on_start()
        text = window._status_label.text()
        assert "INTERPRETER_INVALID" in text
        assert "No interpreter configured." in text
        assert coordinator.spawn_calls == 0  # never launched
    finally:
        window.close()


def test_start_failure_shows_validation_detail_message(qt_app):
    """A typed validation failure surfaces its code and detail message."""
    b = build(FakePopen())
    b.coordinator.validator = lambda python: ValidationOutcome(
        code=ErrorCode.CORE_NOT_INSTALLED,
        python_version=(3, 12, 0),
        distribution_found=False,
        package_found=True,
        detail="castlearq is not installed in the selected interpreter.",
    )
    window = MainWindow(interpreter_settings=b.coordinator.settings,
                        controller=b.controller)
    try:
        window._on_start()
        text = window._status_label.text()
        assert "CORE_NOT_INSTALLED" in text
        assert "castlearq is not installed" in text
    finally:
        window.close()


def test_start_failure_without_error_or_code_is_graceful(qt_app):
    """Missing error objects or codes never crash the UI."""

    class StubController:
        def __init__(self) -> None:
            self.state_changed = FakeSignal()
            self.readiness_result = FakeSignal()
            self.readiness_error = FakeSignal()
            self.shutdown_complete = False

        def start(self):
            return OperationResult(status="failed",
                                   state=LifecycleState.NOT_STARTED)

        def request_shutdown(self) -> None:
            pass

    store = InterpreterSettingsStore(FakeSettings())
    window = MainWindow(interpreter_settings=store,
                        controller=StubController())
    try:
        window._on_start()  # error is None -> safe fallback, no exception
        assert window._status_label.text() == "Core: start failed."

        # An error-like object without a usable code is also tolerated.
        window._controller = SimpleNamespace(
            start=lambda: OperationResult(
                status="failed", state=LifecycleState.NOT_STARTED,
                error=SimpleNamespace()),
            request_shutdown=lambda: None,
            shutdown_complete=False,
        )
        window._on_start()
        assert "start failed" in window._status_label.text()
    finally:
        window.close()


def test_selecting_a_model_does_not_overwrite_the_core_status(qt_app):
    """The model selection line is separate from the Core status line."""
    from castlearq_desktop.core.model_catalog import ModelEntry

    b = build(FakePopen())
    window = MainWindow(interpreter_settings=b.coordinator.settings,
                        controller=b.controller)
    try:
        window._status_label.setText("Core: ready.")
        entry = ModelEntry(
            model_id="qwen2.5-coder-7b-instruct",
            name="Qwen2.5-Coder 7B Instruct",
            provider="Qwen",
            family="Qwen2.5-Coder",
            task="coding",
            architecture="Transformer",
            parameter_count_b=7.0,
            context_length=32768,
            supported_runtimes=("llama.cpp / llama.app",),
            supported_backends=("Vulkan", "CPU"),
            quantizations=("Q4_K_M",),
            downloadable=True,
        )
        window._on_model_selected(entry)
        assert window._status_label.text() == "Core: ready."
        assert "Qwen2.5-Coder 7B Instruct" in window._model_status_label.text()

        window._on_model_unselected()
        assert window._model_status_label.text() == "Ningún modelo seleccionado."
    finally:
        window.close()
def test_external_core_shutdown_does_not_terminate_or_kill(qt_app):
    """An admitted external Core must be closed without terminate/kill calls."""
    b = build(FakePopen())
    b.coordinator._ensure_manager()
    b.controller._external = True
    manager = b.coordinator.manager
    assert manager is not None
    terminate_calls = []
    kill_calls = []
    manager._terminate_process = lambda *a, **k: terminate_calls.append((a, k))
    if hasattr(manager, "_kill_process"):
        manager._kill_process = lambda *a, **k: kill_calls.append((a, k))
    b.controller.request_shutdown()
    assert terminate_calls == []
    assert kill_calls == []
    assert b.controller._external is False
