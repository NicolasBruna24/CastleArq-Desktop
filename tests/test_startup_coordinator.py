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

"""Integration tests: settings → validate → launch → observe → readiness."""

from __future__ import annotations

import json

import pytest

from castlearq_desktop.core.contracts import Deadline
from castlearq_desktop.core.contracts import ErrorCode
from castlearq_desktop.core.contracts import VersionAdmission
from castlearq_desktop.core.interpreter_settings import InterpreterSettingsStore
from castlearq_desktop.core.lifecycle_manager import LifecycleState
from castlearq_desktop.core.readiness_client import HttpProbeResponse
from castlearq_desktop.core.readiness_client import HttpTransportError
from castlearq_desktop.core.readiness_client import ReadinessHttpClient
from castlearq_desktop.core.startup_coordinator import CoreStartupCoordinator
from castlearq_desktop.core.startup_coordinator import manager_liveness

ABS_PYTHON = "/abs/env/bin/python"

#: Test-only admitted version used by these doubles (not production).
TEST_VERSION = "9.9.9-test"


def _health_body(version=TEST_VERSION):
    import json
    return json.dumps({"version": version}).encode()


class FakeSettings:
    def __init__(self):
        self.data = {}

    def value(self, key, defaultValue=None):
        return self.data.get(key, defaultValue)

    def setValue(self, key, value):
        self.data[key] = value

    def remove(self, key):
        self.data.pop(key, None)

    def sync(self):
        pass


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def monotonic(self):
        return self.now

    def advance(self, dt):
        self.now += dt


class FakeSignal:
    def __init__(self):
        self.connections = []

    def connect(self, slot):
        self.connections.append(slot)


class FakeTimer:
    def __init__(self):
        self.timeout = FakeSignal()
        self._interval = None
        self._active = False

    def setInterval(self, ms):
        self._interval = ms

    def isActive(self):
        return self._active

    def start(self):
        self._active = True

    def stop(self):
        self._active = False

    def fire(self):
        for slot in list(self.timeout.connections):
            slot()


class FakePopen:
    def __init__(self, exit_code=None, raises=None):
        self._exit_code = exit_code
        self._raises = raises or {}
        self.terminates = 0
        self.kills = 0

    def poll(self):
        action = self._raises.get("poll")
        if action is not None:
            raise action
        return self._exit_code

    def is_alive_probe(self):
        return self._exit_code is None

    def terminate(self):
        self.terminates += 1

    def kill(self):
        self.kills += 1

    def exit(self, code=0):
        self._exit_code = code


class AlivePopen(FakePopen):
    pass

class PopenFactory:
    def __init__(self, popen):
        self.popen = popen
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.popen


def valid_outcome(python=ABS_PYTHON):
    from castlearq_desktop.core.contracts.interpreter import ValidationOutcome
    return ValidationOutcome(code=None, python_version=(3, 12, 0),
                             distribution_found=True, package_found=True,
                             detail="ok")


def make_coordinator(popen, **kwargs):
    settings = InterpreterSettingsStore(FakeSettings())
    settings.save(ABS_PYTHON)
    clock = FakeClock()
    kwargs.setdefault("clock", clock)
    kwargs.setdefault("popen_factory", PopenFactory(popen))
    kwargs.setdefault("timer_factory", FakeTimer)
    kwargs.setdefault("validator", lambda python: valid_outcome(python))
    kwargs.setdefault("api_contract", VersionAdmission.ADMITTED)
    # DEC-A dynamics are active in the coordinator: these pre-existing
    # doubles exercise 2xx/liveness, so they inject a matching
    # installed-version provider plus an explicit test-only allowlist.
    kwargs.setdefault("installed_version_provider", lambda: TEST_VERSION)
    kwargs.setdefault("supported_versions", frozenset({TEST_VERSION}))
    coord = CoreStartupCoordinator(settings=settings, **kwargs)
    return coord, clock


def make_transport(script):
    calls = []

    def _transport(path, *, timeout):
        calls.append({"path": path, "timeout": timeout})
        action = script.get(path, HttpProbeResponse(200, _health_body()))
        if isinstance(action, Exception):
            raise action
        return action

    _transport.calls = calls
    return _transport


def test_launch_via_existing_interfaces_single_spawn():
    coord, _ = make_coordinator(AlivePopen())
    res = coord.launch()
    assert res.status == "ok"
    assert coord.manager.state is LifecycleState.RUNNING
    assert coord.spawn_calls == 1
    assert coord.observer is not None


def test_pending_when_initial_unknown_then_running_on_liveness():
    coord, _ = make_coordinator(UnknownPopen())
    res = coord.launch()
    assert res.status == "pending"
    assert coord.manager.state is LifecycleState.STARTING
    assert manager_liveness(coord.manager) is None
    from castlearq_desktop.core.subprocess_adapter import SubprocessProcessHandle
    coord.manager._handle = SubprocessProcessHandle(AlivePopen())
    coord.manager.observe()
    assert coord.manager.state is LifecycleState.RUNNING
    assert manager_liveness(coord.manager) is True
    assert coord.spawn_calls == 1


def test_exited_before_readiness_is_not_alive():
    coord, _ = make_coordinator(ExitedPopen(3))
    res = coord.launch()
    assert res.status == "failed"
    assert coord.manager.state is LifecycleState.UNEXPECTED_EXIT
    assert manager_liveness(coord.manager) is False
    out = coord.check_readiness_once(now=0.0)
    assert out.ready is False
    assert out.error_code is None

def test_exited_during_readiness_is_not_ready():
    coord, _ = make_coordinator(AlivePopen())
    coord.launch()
    coord.transport = make_transport({})
    coord.manager._handle._popen.exit(5)
    coord.manager.observe()
    assert coord.manager.state is LifecycleState.UNEXPECTED_EXIT
    out = coord.check_readiness_once(now=0.0)
    assert out.ready is False


def test_unknown_liveness_is_fail_closed_without_code():
    coord, _ = make_coordinator(UnknownPopen())
    coord.launch()
    coord.transport = make_transport({})
    out = coord.check_readiness_once(now=0.0)
    assert out.ready is False
    assert out.error_code is None


def test_ready_only_when_all_conditions_met():
    coord, _ = make_coordinator(AlivePopen())
    coord.launch()
    coord.transport = make_transport({})
    out = coord.check_readiness_once(now=0.0)
    assert out.ready is True
    assert out.error_code is None


def test_error_codes_propagate_by_stage():
    from castlearq_desktop.core.contracts import HEALTH_PATH
    coord, _ = make_coordinator(AlivePopen())
    coord.launch()
    coord.transport = make_transport({HEALTH_PATH: HttpProbeResponse(
        503, _health_body())})
    out = coord.check_readiness_once(now=0.0)
    assert out.ready is False
    # Version matches and is allowlisted, so only the health stage fails.
    assert out.error_code is ErrorCode.HEALTH_CHECK_FAILED


def test_deadline_expired_never_ready():
    coord, _ = make_coordinator(AlivePopen())
    coord.launch()
    coord.transport = make_transport({})
    deadline = Deadline.starting_now(0.0)
    out = coord.check_readiness(now=25.0, deadline=deadline)
    assert out.ready is False
    assert out.error_code is ErrorCode.READINESS_TIMEOUT
    assert out.deadline_expired is True


def test_launch_starting_starts_observer():
    coord, _ = make_coordinator(AlivePopen())
    coord.launch()
    assert coord.observer is not None
    assert coord.observer.is_running() is True
    assert coord.observer.timer is not None
    assert coord.observer.timer._active is True


def test_launch_running_starts_observer():
    coord, _ = make_coordinator(AlivePopen())
    coord.launch()
    assert coord.observer is not None
    assert coord.observer.is_running() is True


def test_definite_failure_does_not_start_timer():
    coord, _ = make_coordinator(ExitedPopen(code=3))
    coord.launch()
    assert coord.observer is None
    assert coord.observer is None or coord.observer.is_running() is False


def test_repeated_initialization_no_duplicates():
    coord, _ = make_coordinator(AlivePopen())
    coord.launch()
    coord.launch()
    assert coord.observer is not None
    assert coord.observer.is_running() is True
    # single timer, single connection
    assert coord.observer.timer is not None
    assert len(coord.observer.timer.timeout.connections) == 1


def test_original_launch_result_preserved():
    coord, _ = make_coordinator(AlivePopen())
    result = coord.launch()
    assert result.state is LifecycleState.RUNNING
    assert coord.observer is not None
    assert coord.observer.is_running() is True


def test_unknown_liveness_fail_closed():
    from castlearq_desktop.core.contracts import VersionAdmission
    from castlearq_desktop.core.readiness_client import ReadinessWaitResult
    coord, _ = make_coordinator(UnknownPopen(), api_contract=VersionAdmission.NOT_EVALUATED)
    coord.launch()
    assert coord.observer is not None
    assert coord.observer.is_running() is True
    client = ReadinessHttpClient(
        transport=coord.transport, clock=coord.clock, sleeper=coord.sleeper,
        api_contract=coord.api_contract, child_observer=coord._liveness)
    result = client.wait_until_ready(now=0.0, deadline=Deadline.starting_now(0.0))
    assert result.ready is False
    # liveness is unknown (None), so fail-closed
    assert manager_liveness(coord.manager) in (None, False)

    coord, _ = make_coordinator(AlivePopen())
    coord.launch()
    transport = make_transport({"/health": HttpTransportError("down")})
    coord.transport = transport
    out = coord.check_readiness_once(now=0.0)
    assert out.ready is False
    assert len([c for c in transport.calls if c["path"] == "/health"]) == 1
    assert coord.spawn_calls == 1
    second = coord.manager.start(ABS_PYTHON)
    assert second.status == "refused"
    assert coord.spawn_calls == 1


def test_no_signals_no_policy_change():
    import pathlib
    coord, _ = make_coordinator(AlivePopen())
    coord.launch()
    popen = coord.manager._handle._popen
    assert popen.terminates == 0
    assert popen.kills == 0
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "src/castlearq_desktop/core/startup_coordinator.py").read_text()
    for forbidden in (".kill()", "SIGKILL", "shell=True",
                      "pip install", "QThread", "concurrent.futures"):
        assert forbidden not in src
    assert "GRACEFUL_TERMINATION_TIMEOUT_SECONDS = " not in src


def test_urllib_limitation_stays_documented():
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "src/castlearq_desktop/core/readiness_client.py").read_text()
    assert "CANNOT" in src and "preempt" in src


def test_unconfigured_interpreter_not_launched():
    settings = InterpreterSettingsStore(FakeSettings())
    coord = CoreStartupCoordinator(settings=settings, clock=FakeClock(),
                                   timer_factory=FakeTimer,
                                   validator=lambda p: valid_outcome(p))
    res = coord.launch()
    assert res.status == "failed"
    assert coord.spawn_calls == 0


class ExitedPopen(FakePopen):
    def __init__(self, code=3):
        super().__init__(exit_code=code)


class UnknownPopen:
    def __init__(self):
        self.terminates = 0
        self.kills = 0

    def poll(self):
        raise RuntimeError("poll down")

    def is_alive(self):
        raise RuntimeError("status down")

    def terminate(self):
        self.terminates += 1

    def kill(self):
        self.kills += 1


# --- Asynchronous readiness (Qt) integration --------------------------------
try:
    from castlearq_desktop.core.readiness_qt_transport import QtReadinessChecker
    from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

    _QT = True
except Exception:  # pragma: no cover - environment without PySide6
    _QT = False

qt_required = pytest.mark.skipif(not _QT, reason="PySide6.QtNetwork unavailable")


class _QtSig:
    def __init__(self):
        self.connections = []

    def connect(self, slot):
        self.connections.append(slot)

    def emit(self, *args):
        for slot in list(self.connections):
            slot(*args)


class QtFakeReply:
    def __init__(self):
        self.finished = _QtSig()
        self._error = QNetworkReply.NetworkError.NoError
        self._status = None
        self.aborted = False

    def error(self):
        return self._error

    def attribute(self, attr):
        if attr == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self._status
        return None

    def abort(self):
        self.aborted = True
        self._error = QNetworkReply.NetworkError.OperationCanceledError
        self.finished.emit()

    def deleteLater(self):
        pass

    def succeed(self, status):
        self._error = QNetworkReply.NetworkError.NoError
        self._status = status
        self.finished.emit()

    def http_error(self, status):
        self._error = QNetworkReply.NetworkError.ProtocolFailure
        self._status = status
        self.finished.emit()

    def readAll(self):
        # DEC-A: the Qt transport reads the /health body via readAll() to
        # extract the top-level "version". This double returns a well-formed
        # body whose version matches the installed version simulated by
        # make_coordinator, so admission is evaluated for real rather than
        # short-circuited by an absent body.
        return _health_body()


class QtFakeNAM:
    def __init__(self):
        self.requests = []

    def get(self, request):
        reply = QtFakeReply()
        self.requests.append((request.url().toString(), reply))
        return reply


@qt_required
def test_check_readiness_async_delivers_ready_via_signal():
    nam = QtFakeNAM()
    coord, _ = make_coordinator(AlivePopen(), qt_nam=nam)
    coord.launch()
    assert coord.manager.state is LifecycleState.RUNNING
    checker = coord.check_readiness_async(now=0.0)
    assert isinstance(checker, QtReadinessChecker)
    got = []
    checker.result.connect(lambda r: got.append(r))
    # Non-blocking: nothing delivered until the simulated replies complete.
    assert got == []
    assert len(nam.requests) == 3
    for _, r in nam.requests:
        r.succeed(200)
    assert len(got) == 1
    assert got[0].ready is True
    assert coord._last_readiness_result is got[0]


@qt_required
def test_check_readiness_async_unknown_liveness_not_ready():
    nam = QtFakeNAM()
    coord, _ = make_coordinator(UnknownPopen(), qt_nam=nam,
                                api_contract=VersionAdmission.ADMITTED)
    coord.launch()
    assert manager_liveness(coord.manager) is None
    checker = coord.check_readiness_async(now=0.0)
    got = []
    checker.result.connect(lambda r: got.append(r))
    for _, r in nam.requests:
        r.succeed(200)
    # Unknown liveness is fail-closed: never a terminal ready result.
    assert got == []
    assert coord.spawn_calls == 1
    checker.stop()


@qt_required
def test_check_readiness_async_no_process_signals_or_relaunch():
    nam = QtFakeNAM()
    coord, _ = make_coordinator(AlivePopen(), qt_nam=nam)
    coord.launch()
    popen = coord.manager._handle._popen
    checker = coord.check_readiness_async(now=0.0)
    for _, r in nam.requests:
        r.succeed(200)
    assert popen.terminates == 0
    assert popen.kills == 0
    assert coord.spawn_calls == 1
    assert coord.manager.start(ABS_PYTHON).status == "refused"
    checker.stop()
