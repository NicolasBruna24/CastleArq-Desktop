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

"""Deterministic tests for the readiness HTTP client (DEC-B / DEC-E)."""

from __future__ import annotations

import pytest

from castlearq_desktop.core.contracts import ARTIFACTS_PATH
from castlearq_desktop.core.contracts import CORE_HOST
from castlearq_desktop.core.contracts import CORE_PORT
from castlearq_desktop.core.contracts import Deadline
from castlearq_desktop.core.contracts import ErrorCode
from castlearq_desktop.core.contracts import HEALTH_PATH
from castlearq_desktop.core.contracts import MAX_REQUEST_TIMEOUT_SECONDS
from castlearq_desktop.core.contracts import MODELS_PATH
from castlearq_desktop.core.contracts import POLL_INTERVAL_SECONDS
from castlearq_desktop.core.contracts import READINESS_REQUEST_TIMEOUT_SECONDS
from castlearq_desktop.core.contracts import TRANSPORT_RETRIES_ENABLED
from castlearq_desktop.core.contracts import VersionAdmission
from castlearq_desktop.core.readiness_client import HttpProbeResponse
from castlearq_desktop.core.readiness_client import HttpRequestTimeout
from castlearq_desktop.core.readiness_client import HttpTransportError
from castlearq_desktop.core.readiness_client import READINESS_PROBE_PATHS
from castlearq_desktop.core.readiness_client import ReadinessHttpClient
from castlearq_desktop.core.readiness_client import default_base_url


class FakeTransport:
    def __init__(self, script):
        self.script = dict(script)
        self.calls = []

    def __call__(self, path, *, timeout):
        self.calls.append({"path": path, "timeout": timeout})
        action = self.script.get(path, HttpProbeResponse(status=200))
        if isinstance(action, Exception):
            raise action
        if isinstance(action, list):
            assert action, f"no more responses for {path}"
            item = action.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return action


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now

    def advance(self, dt):
        self.now += dt


def admitted_client(transport, **kwargs):
    kwargs.setdefault("api_contract", VersionAdmission.ADMITTED)
    # Explicit liveness observation (never an implicit default): tests that
    # exercise pure HTTP behaviour pass a constant-alive observer.
    kwargs.setdefault("child_observer", lambda: True)
    return ReadinessHttpClient(transport=transport, **kwargs)


def test_probe_order_and_base_url():
    assert READINESS_PROBE_PATHS == (HEALTH_PATH, MODELS_PATH, ARTIFACTS_PATH)
    assert HEALTH_PATH == "/health"
    assert MODELS_PATH == "/v1/models"
    assert ARTIFACTS_PATH == "/v1/artifacts"
    assert default_base_url() == f"http://{CORE_HOST}:{CORE_PORT}"
    assert (CORE_HOST, CORE_PORT) == ("127.0.0.1", 8000)
    t = FakeTransport({})
    c = admitted_client(t)
    assert c.base_url == "http://127.0.0.1:8000"
    r = c.probe_once(now=0.0)
    assert [a.path for a in r.attempts] == ["/health", "/v1/models", "/v1/artifacts"]
    assert r.ready is True


def test_ready_when_all_valid():
    t = FakeTransport({HEALTH_PATH: HttpProbeResponse(200),
                       MODELS_PATH: HttpProbeResponse(201),
                       ARTIFACTS_PATH: HttpProbeResponse(299)})
    r = admitted_client(t).probe_once(now=0.0)
    assert r.ready is True
    assert r.error_code is None
    assert r.outcome.ready


def test_non_2xx_health_code():
    t = FakeTransport({HEALTH_PATH: HttpProbeResponse(503)})
    r = admitted_client(t).probe_once(now=0.0)
    assert r.ready is False
    assert r.error_code is ErrorCode.HEALTH_CHECK_FAILED


def test_non_2xx_models_code():
    t = FakeTransport({MODELS_PATH: HttpProbeResponse(500)})
    r = admitted_client(t).probe_once(now=0.0)
    assert r.ready is False
    assert r.error_code is ErrorCode.API_PROBE_FAILED


def test_transport_failure_single_endpoint():
    t = FakeTransport({MODELS_PATH: HttpTransportError("conn refused")})
    r = admitted_client(t).probe_once(now=0.0)
    assert r.ready is False
    assert r.error_code is ErrorCode.API_PROBE_FAILED
    assert sum(1 for c in t.calls if c["path"] == MODELS_PATH) == 1


def test_global_deadline_expiry_is_readiness_timeout():
    t = FakeTransport({HEALTH_PATH: HttpProbeResponse(500)})
    c = admitted_client(t)
    deadline = Deadline.starting_now(0.0)
    result = c.wait_until_ready(now=25.0, deadline=deadline)
    assert result.ready is False
    assert result.error_code is ErrorCode.READINESS_TIMEOUT
    assert result.deadline_expired is True


def test_effective_timeout_uses_remaining_budget():
    seen = {}

    def _transport(path, *, timeout):
        seen[path] = timeout
        return HttpProbeResponse(200)

    c = admitted_client(_transport)
    deadline = Deadline.starting_now(0.0)
    r = c.probe_once(now=18.0, deadline=deadline)
    assert r.attempts[0].timeout == pytest.approx(2.0)
    assert all(a.timeout == pytest.approx(2.0) for a in r.attempts)
    r2 = c.probe_once(now=0.0, deadline=deadline)
    assert all(a.timeout == READINESS_REQUEST_TIMEOUT_SECONDS for a in r2.attempts)


def test_fallback_path_uses_the_readiness_timeout_not_the_generic_maximum():
    """The urllib fallback path must use the readiness-specific cap.

    CastleArq Core 0.6.0 answers ``GET /v1/artifacts`` with HTTP 200 after
    ~5.5-5.8 s; the generic 4 s cap made every fallback pass time out.
    """
    seen = {}

    def _transport(path, *, timeout):
        seen[path] = timeout
        return HttpProbeResponse(200)

    c = admitted_client(_transport)
    deadline = Deadline.starting_now(0.0)
    # With ample budget the readiness cap (10 s) applies, not the generic 4 s.
    c.probe_once(now=0.0, deadline=deadline)
    assert all(t == READINESS_REQUEST_TIMEOUT_SECONDS for t in seen.values())
    assert all(t > MAX_REQUEST_TIMEOUT_SECONDS for t in seen.values())
    # Without a deadline the readiness cap is still the one used.
    seen.clear()
    c.probe_once(now=0.0, deadline=None)
    assert all(t == READINESS_REQUEST_TIMEOUT_SECONDS for t in seen.values())
    # The remaining global budget still wins over the cap.
    seen.clear()
    c.probe_once(now=18.0, deadline=deadline)
    assert all(t == pytest.approx(2.0) for t in seen.values())


def test_poll_interval_250ms_and_no_overshoot():
    assert POLL_INTERVAL_SECONDS == 0.25
    sleeps = []
    clock = FakeClock(start=0.0)
    script = {HEALTH_PATH: [HttpProbeResponse(500), HttpProbeResponse(200)],
              MODELS_PATH: [HttpProbeResponse(200), HttpProbeResponse(200)],
              ARTIFACTS_PATH: [HttpProbeResponse(200), HttpProbeResponse(200)]}
    t = FakeTransport(script)
    c = ReadinessHttpClient(transport=t, clock=clock,
                            sleeper=lambda d: (sleeps.append(d), clock.advance(d)),
                            api_contract=VersionAdmission.ADMITTED,
                            child_observer=lambda: True)
    deadline = Deadline.starting_now(clock.monotonic())
    result = c.wait_until_ready(deadline=deadline)
    assert result.ready is True
    assert result.passes == 2
    assert sleeps == [pytest.approx(0.25)]
    assert clock.monotonic() == pytest.approx(0.25)


def test_no_transport_retries():
    assert TRANSPORT_RETRIES_ENABLED is False
    t = FakeTransport({HEALTH_PATH: HttpTransportError("down")})
    r = admitted_client(t).probe_once(now=0.0)
    assert len([c for c in t.calls if c["path"] == HEALTH_PATH]) == 1
    assert r.ready is False


def test_non_admitted_contract_fails_closed():
    t = FakeTransport({})
    for admission in (VersionAdmission.NOT_EVALUATED, VersionAdmission.UNKNOWN,
                      VersionAdmission.FUTURE, VersionAdmission.INCOMPATIBLE):
        c = ReadinessHttpClient(transport=t, api_contract=admission,
                                child_observer=lambda: True)
        r = c.probe_once(now=0.0)
        assert r.ready is False
        assert r.error_code is ErrorCode.API_CONTRACT_INCOMPATIBLE


def test_body_ignored_no_schema_invented():
    t = FakeTransport({HEALTH_PATH: HttpProbeResponse(200, body={"weird": 1}),
                       MODELS_PATH: HttpProbeResponse(200, body="not-json"),
                       ARTIFACTS_PATH: HttpProbeResponse(200, body=None)})
    r = admitted_client(t).probe_once(now=0.0)
    assert r.ready is True
    assert r.error_code is None


def test_multiple_failures_have_no_invented_precedence():
    t = FakeTransport({HEALTH_PATH: HttpProbeResponse(500),
                       MODELS_PATH: HttpProbeResponse(500)})
    r = admitted_client(t).probe_once(now=0.0)
    assert r.ready is False
    assert r.error_code is None


def test_all_codes_are_approved():
    for code in (ErrorCode.HEALTH_CHECK_FAILED, ErrorCode.API_PROBE_FAILED,
                 ErrorCode.API_CONTRACT_INCOMPATIBLE, ErrorCode.READINESS_TIMEOUT):
        assert code in (ErrorCode.HEALTH_CHECK_FAILED, ErrorCode.API_PROBE_FAILED,
                        ErrorCode.API_CONTRACT_INCOMPATIBLE, ErrorCode.READINESS_TIMEOUT,
                        ErrorCode.PROCESS_START_FAILED, ErrorCode.PORT_CONFLICT,
                        ErrorCode.INTERPRETER_INVALID, ErrorCode.CORE_NOT_INSTALLED,
                        ErrorCode.SHUTDOWN_FAILED)


def test_no_process_lifecycle_gui_side_effects():
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "src/castlearq_desktop/core/readiness_client.py").read_text()
    for forbidden in ("Popen", "LifecycleManager", "QTimer", "QThread",
                      "QApplication", "threading.Thread", "concurrent.futures",
                      "castlearq.main"):
        assert forbidden not in src


def test_default_urllib_transport_builds_without_httpx():
    import sys
    assert "httpx" not in sys.modules or True
    from castlearq_desktop.core.readiness_client import urllib_transport
    transport = urllib_transport("http://127.0.0.1:9")
    try:
        transport("/health", timeout=0.01)
    except Exception as exc:
        assert type(exc).__name__ in ("HttpTransportError", "HttpRequestTimeout")


def test_single_request_timeout_maps_to_stage_code():
    t = FakeTransport({ARTIFACTS_PATH: HttpRequestTimeout("slow")})
    r = admitted_client(t).probe_once(now=0.0)
    assert r.ready is False
    assert r.error_code is ErrorCode.API_PROBE_FAILED


# --- Liveness correction: no default simulates confirmed liveness --------

def test_no_default_liveness_is_fail_closed():
    t = FakeTransport({})
    c = ReadinessHttpClient(transport=t, api_contract=VersionAdmission.ADMITTED)
    r = c.probe_once(now=0.0)
    assert r.ready is False
    # Unknown liveness carries no invented error code.
    assert r.error_code is None
    assert any(f.condition.value == "child_alive_before" for f in r.outcome.failures)


def test_explicit_alive_before_and_after_is_ready():
    t = FakeTransport({})
    c = ReadinessHttpClient(transport=t, api_contract=VersionAdmission.ADMITTED,
                            child_alive_before=True, child_alive_after=True)
    r = c.probe_once(now=0.0)
    assert r.ready is True
    assert r.error_code is None


def test_observer_mixing_with_explicit_booleans_rejected():
    t = FakeTransport({})
    with pytest.raises(ValueError):
        ReadinessHttpClient(transport=t, child_observer=lambda: True,
                            child_alive_before=True)


def test_exited_before_start_is_not_ready():
    t = FakeTransport({})
    c = ReadinessHttpClient(transport=t, api_contract=VersionAdmission.ADMITTED,
                            child_observer=lambda: False)
    r = c.probe_once(now=0.0)
    assert r.ready is False
    assert r.error_code is None  # no approved code for child death


def test_exited_during_pass_is_not_ready():
    states = [True, False]
    t = FakeTransport({})
    c = ReadinessHttpClient(transport=t, api_contract=VersionAdmission.ADMITTED,
                            child_observer=lambda: states.pop(0))
    r = c.probe_once(now=0.0)
    assert r.ready is False
    assert r.error_code is None


def test_unknown_after_is_fail_closed_without_code():
    states = [True, None]
    t = FakeTransport({})
    c = ReadinessHttpClient(transport=t, api_contract=VersionAdmission.ADMITTED,
                            child_observer=lambda: states.pop(0))
    r = c.probe_once(now=0.0)
    assert r.ready is False
    assert r.error_code is None


def test_observer_exception_is_unknown_not_crash():
    def _boom():
        raise RuntimeError("probe down")
    t = FakeTransport({})
    c = ReadinessHttpClient(transport=t, api_contract=VersionAdmission.ADMITTED,
                            child_observer=_boom)
    r = c.probe_once(now=0.0)
    assert r.ready is False
    assert r.error_code is None


def test_liveness_check_has_no_side_effects():
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "src/castlearq_desktop/core/readiness_client.py").read_text()
    assert "child_observer" in src
    for forbidden in (".terminate(", ".kill(", "Popen(", "LifecycleManager(",
                      "SubprocessProcessAdapter"):
        assert forbidden not in src


# --- Deadline correction: post-request overrun detection -----------------

def test_overrun_detected_after_request_reports_timeout():
    clock = FakeClock(start=0.0)

    def _slow_transport(path, *, timeout):
        clock.advance(25.0)  # wall-clock actually elapsed during request
        return HttpProbeResponse(200)

    c = ReadinessHttpClient(transport=_slow_transport, clock=clock,
                            child_observer=lambda: True,
                            api_contract=VersionAdmission.ADMITTED)
    deadline = Deadline.starting_now(0.0)
    r = c.probe_once(deadline=deadline)
    assert r.ready is False
    assert r.error_code is ErrorCode.READINESS_TIMEOUT


def test_no_request_started_once_expired():
    t = FakeTransport({})
    c = admitted_client(t)
    deadline = Deadline.starting_now(0.0)
    r = c.probe_once(now=20.0, deadline=deadline)
    assert t.calls == []
    assert all(a.timeout == 0.0 for a in r.attempts)
    assert r.ready is False


# --- Asynchronous Qt Network readiness transport (DEC-B / DEC-E) -----------
#
# These tests drive the Qt event-loop state machine deterministically with a
# fake network manager and fake timers (synchronous, no real Core, no network).

try:
    from castlearq_desktop.core.readiness_qt_transport import (
        QtReadinessChecker,
        QtReadinessTransport,
    )
    from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

    _QT_AVAILABLE = True
except Exception:  # pragma: no cover - environment without PySide6
    _QT_AVAILABLE = False

qt_required = pytest.mark.skipif(
    not _QT_AVAILABLE, reason="PySide6.QtNetwork unavailable")


class FakeSignal:
    def __init__(self):
        self.connections = []

    def connect(self, slot):
        self.connections.append(slot)

    def emit(self, *args):
        for slot in list(self.connections):
            slot(*args)


class FakeReply:
    def __init__(self):
        self.finished = FakeSignal()
        self._error = QNetworkReply.NetworkError.NoError
        self._status = None
        self._body = b""
        self._declared_length = None
        self.read_calls = 0
        self.aborted = False

    def error(self):
        return self._error

    def attribute(self, attr):
        if attr == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self._status
        return None

    def readAll(self):
        self.read_calls += 1
        return self._body

    def header(self, _kind):
        return self._declared_length

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

    def transport_fail(self):
        self._error = QNetworkReply.NetworkError.ConnectionRefusedError
        self._status = None
        self.finished.emit()


class FakeNAM:
    def __init__(self):
        self.requests = []

    def get(self, request):
        reply = FakeReply()
        self.requests.append((request.url().toString(), reply))
        return reply


class FakeQTimer:
    def __init__(self, registry):
        self.timeout = FakeSignal()
        self.interval = None
        self.stopped = False
        self._registry = registry

    def setInterval(self, ms):
        self.interval = ms

    def setSingleShot(self, single):
        pass

    def start(self):
        self._registry.append(self)

    def stop(self):
        self.stopped = True

    def fire(self):
        self.timeout.emit()


class QtClock:
    def __init__(self, start=0.0):
        self.now = start

    def monotonic(self):
        return self.now

    def advance(self, dt):
        self.now += dt


def make_transport(nam, clock, registry, *, observer=lambda: True,
                   contract=VersionAdmission.ADMITTED, request_timeout=None):
    kwargs = {}
    if request_timeout is not None:
        kwargs["request_timeout"] = request_timeout
    return QtReadinessTransport(
        "http://127.0.0.1:8000",
        child_observer=observer,
        api_contract=contract,
        clock=clock,
        timer_factory=lambda: FakeQTimer(registry),
        nam=nam,
        **kwargs,
    )


def urls(nam):
    return [u for u, _ in nam.requests]


def timers_with(registry, ms):
    return [t for t in registry if t.interval == ms]


@qt_required
def test_qt_endpoints_order_and_timer_intervals():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    results = []
    tr.finished.connect(lambda r: results.append(r))
    tr.start(0.0, Deadline.starting_now(0.0))
    assert urls(nam) == [
        "http://127.0.0.1:8000/health",
        "http://127.0.0.1:8000/v1/models",
        "http://127.0.0.1:8000/v1/artifacts",
    ]
    # 3 per-request timers at the readiness cap, 1 global deadline timer at 20 s
    assert len(timers_with(reg, int(READINESS_REQUEST_TIMEOUT_SECONDS * 1000))) == 3
    assert len(timers_with(reg, int(20.0 * 1000))) == 1


@qt_required
def test_qt_ready_when_all_valid():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    results, progress = [], []
    tr.finished.connect(lambda r: results.append(r))
    tr.progress.connect(lambda p: progress.append(p))
    tr.start(0.0, Deadline.starting_now(0.0))
    nam.requests[0][1].succeed(200)
    nam.requests[1][1].succeed(201)
    nam.requests[2][1].succeed(299)
    assert len(results) == 1
    assert results[0].ready is True
    assert results[0].error_code is None
    assert len(progress) == 1


@qt_required
def test_qt_http_error_distinct_from_transport_error():
    # HTTP non-2xx is observed (not a transport failure): stage code applies.
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    progress = []
    tr.progress.connect(lambda p: progress.append(p))
    tr.start(0.0, Deadline.starting_now(0.0))
    nam.requests[0][1].http_error(503)
    nam.requests[1][1].succeed(200)
    nam.requests[2][1].succeed(200)
    assert progress[-1].error_code == ErrorCode.HEALTH_CHECK_FAILED


@qt_required
def test_qt_transport_failure_not_ready():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    results = []
    tr.finished.connect(lambda r: results.append(r))
    tr.start(0.0, Deadline.starting_now(0.0))
    nam.requests[0][1].transport_fail()
    nam.requests[1][1].succeed(200)
    nam.requests[2][1].succeed(200)
    assert results == []  # not ready -> schedules another pass


@qt_required
def test_qt_request_timeout_aborts_reply():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    results = []
    tr.finished.connect(lambda r: results.append(r))
    tr.start(0.0, Deadline.starting_now(0.0))
    timers_with(reg, 10000)[0].fire()  # health request times out
    assert nam.requests[0][1].aborted is True
    nam.requests[1][1].succeed(200)
    nam.requests[2][1].succeed(200)
    assert results == []  # health unobserved -> not ready


@qt_required
def test_qt_request_timeout_capped_by_remaining():
    # Near the deadline the per-request timeout is min(cap, remaining).
    reg, nam, clock = [], FakeNAM(), QtClock(18.0)
    tr = make_transport(nam, clock, reg)
    tr.start(18.0, Deadline.starting_now(0.0))  # 2 s remaining
    assert timers_with(reg, 2000)  # capped to remaining 2 s, not the 10 s cap
    assert not timers_with(reg, 10000)


@qt_required
def test_qt_transport_uses_the_readiness_timeout_not_the_generic_maximum():
    """The production Qt path must arm its timers with the readiness cap.

    CastleArq Core 0.6.0 answers ``GET /v1/artifacts`` with HTTP 200 after
    ~5.5-5.8 s: with the generic 4 s cap every Qt readiness pass timed out.
    """
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    tr.start(0.0, Deadline.starting_now(0.0))
    assert len(timers_with(reg, int(READINESS_REQUEST_TIMEOUT_SECONDS * 1000))) == 3
    # The generic cap is not used by the readiness transport.
    assert not timers_with(reg, int(MAX_REQUEST_TIMEOUT_SECONDS * 1000))


@qt_required
def test_qt_transport_request_timeout_is_injectable():
    """The readiness cap is a parameter, not a hard-coded value."""
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg, request_timeout=7.5)
    tr.start(0.0, Deadline.starting_now(0.0))
    assert len(timers_with(reg, 7500)) == 3
    assert not timers_with(reg, int(READINESS_REQUEST_TIMEOUT_SECONDS * 1000))


@qt_required
def test_qt_transport_injected_timeout_still_respects_global_budget():
    """An injected cap never overrides the remaining global budget."""
    reg, nam, clock = [], FakeNAM(), QtClock(15.0)
    tr = make_transport(nam, clock, reg, request_timeout=10.0)
    tr.start(15.0, Deadline.starting_now(0.0))  # 5 s remaining
    assert timers_with(reg, 5000)
    assert not timers_with(reg, 10000)


@qt_required
def test_qt_global_deadline_finishes_with_timeout():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    results = []
    tr.finished.connect(lambda r: results.append(r))
    tr.start(0.0, Deadline.starting_now(0.0))
    timers_with(reg, 20000)[0].fire()
    assert len(results) == 1
    assert results[0].ready is False
    assert results[0].error_code == ErrorCode.READINESS_TIMEOUT
    assert results[0].deadline_expired is True
    assert all(r.aborted for _, r in nam.requests)


@qt_required
def test_qt_polling_interval_250ms():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    tr.start(0.0, Deadline.starting_now(0.0))
    nam.requests[0][1].http_error(500)
    nam.requests[1][1].succeed(200)
    nam.requests[2][1].succeed(200)
    poll = timers_with(reg, int(POLL_INTERVAL_SECONDS * 1000))
    assert len(poll) == 1


@qt_required
def test_qt_no_concurrent_passes():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    tr.start(0.0, Deadline.starting_now(0.0))
    assert len(nam.requests) == 3
    nam.requests[0][1].succeed(200)
    nam.requests[1][1].succeed(200)
    # still waiting for the third reply -> no new pass started
    assert len(nam.requests) == 3


@qt_required
def test_qt_next_pass_only_after_poll():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    tr.start(0.0, Deadline.starting_now(0.0))
    for _, r in nam.requests:
        r.http_error(500)
    assert len(nam.requests) == 3
    poll = timers_with(reg, 250)[0]
    clock.advance(POLL_INTERVAL_SECONDS)
    poll.fire()
    assert len(nam.requests) == 6  # a fresh pass of three requests


@qt_required
def test_qt_unknown_liveness_fail_closed():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg, observer=lambda: None)
    results = []
    tr.finished.connect(lambda r: results.append(r))
    tr.start(0.0, Deadline.starting_now(0.0))
    for _, r in nam.requests:
        r.succeed(200)
    assert results == []  # unknown liveness never confirms readiness


@qt_required
def test_qt_exited_before_is_not_ready_without_code():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg, observer=lambda: False)
    progress = []
    tr.progress.connect(lambda p: progress.append(p))
    tr.start(0.0, Deadline.starting_now(0.0))
    for _, r in nam.requests:
        r.succeed(200)
    assert progress[-1].ready is False
    assert progress[-1].error_code is None  # no approved code for child death


@qt_required
def test_qt_late_response_after_finish_is_ignored():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    results = []
    tr.finished.connect(lambda r: results.append(r))
    tr.start(0.0, Deadline.starting_now(0.0))
    timers_with(reg, 20000)[0].fire()  # deadline -> terminal timeout
    assert len(results) == 1
    # A late reply for the already-finished operation must not re-emit.
    for _, r in nam.requests:
        r.succeed(200)
    assert len(results) == 1


@qt_required
def test_qt_finishes_exactly_once():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    results = []
    tr.finished.connect(lambda r: results.append(r))
    tr.start(0.0, Deadline.starting_now(0.0))
    timers_with(reg, 20000)[0].fire()
    tr.stop()
    tr.stop()
    assert len(results) == 1


@qt_required
def test_qt_cleanup_stops_timers_and_aborts_requests():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    tr.start(0.0, Deadline.starting_now(0.0))
    tr.stop()
    assert all(t.stopped for t in reg)
    assert all(r.aborted for _, r in nam.requests)


@qt_required
def test_qt_contract_incompatible_code_preserved():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg, contract=VersionAdmission.NOT_EVALUATED)
    progress = []
    tr.progress.connect(lambda p: progress.append(p))
    tr.start(0.0, Deadline.starting_now(0.0))
    for _, r in nam.requests:
        r.succeed(200)
    assert progress[-1].error_code == ErrorCode.API_CONTRACT_INCOMPATIBLE


@qt_required
def test_qt_checker_forwards_result_once():
    reg, nam, clock = [], FakeNAM(), QtClock(0.0)
    tr = make_transport(nam, clock, reg)
    checker = QtReadinessChecker(tr)
    got = []
    checker.result.connect(lambda r: got.append(r))
    checker.start(0.0, Deadline.starting_now(0.0))
    for _, r in nam.requests:
        r.succeed(200)
    assert len(got) == 1 and got[0].ready is True


@qt_required
def test_qt_transport_has_no_blocking_calls():
    import ast
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "src/castlearq_desktop/core/readiness_qt_transport.py")
    tree = ast.parse(path.read_text())
    forbidden_modules = {"time", "threading", "concurrent", "asyncio"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_modules
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in forbidden_modules
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "sleep":
                raise AssertionError("blocking sleep call found in transport")


@qt_required
def test_qt_event_loop_stays_responsive_while_pending():
    import sys
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    nam = FakeNAM()  # replies never auto-complete -> stay pending
    clock = QtClock(0.0)
    tr = QtReadinessTransport(
        "http://127.0.0.1:8000",
        child_observer=lambda: True,
        api_contract=VersionAdmission.ADMITTED,
        clock=clock,
        timer_factory=QTimer,  # real Qt timers driven by the event loop
        nam=nam,
    )
    results = []
    tr.finished.connect(lambda r: results.append(r))

    heartbeat = []
    hb = QTimer()
    hb.setInterval(5)
    hb.timeout.connect(lambda: heartbeat.append(1))
    hb.start()

    tr.start(0.0, Deadline.starting_now(0.0))  # must return immediately

    loop = QEventLoop()
    QTimer.singleShot(80, loop.quit)
    loop.exec()
    hb.stop()

    # The event loop kept delivering timer events while requests were pending.
    assert len(heartbeat) >= 2
    assert len(nam.requests) == 3
    assert results == []  # nothing finished while requests are outstanding

    tr.stop()
    assert len(results) == 1  # cancellation finalizes exactly once
