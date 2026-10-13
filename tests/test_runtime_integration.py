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

"""Isolated, opt-in runtime integration tests for the CastleArq Desktop startup,
readiness, and shutdown integration.

These tests exercise the *real* production components on a *real* Qt event loop:

* the real ``QtReadinessTransport`` with a real ``QNetworkAccessManager`` against
  a loopback-only HTTP test server on an OS-assigned ephemeral port;
* the real ``SubprocessProcessAdapter``, ``LifecycleManager`` and
  ``LifecycleObserver`` driving a purpose-built, short-lived test subprocess
  (never CastleArq Core);
* the real ``StartupController`` shutdown coordination.

Explicit opt-in. Nothing runs unless ``CASTLEARQ_RUNTIME_IT=1`` is set in the
environment. When the gate is absent every test skips *before* creating any Qt
application, thread, socket, subprocess, or file. Importing this module has no
runtime side effects.

Isolation guarantees:
* loopback-only server on an ephemeral port (no fixed port, no public bind);
* no external network, no real Core process, no persistent files, no user
  settings changes;
* DEC-A is never approved: the production default ``VersionAdmission.NOT_EVALUATED``
  remains fail-closed. A single, clearly-isolated case uses a test-only
  ``ADMITTED`` value purely to exercise the transport's successful path;
* every asynchronous wait is bounded by a watchdog; every owned resource has an
  explicit, bounded cleanup path in ``finally`` blocks.

The harness watchdog is *not* the production shutdown deadline and is never
reported as proof of a production shutdown outcome.
"""

from __future__ import annotations

import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from castlearq_desktop.core.contracts import Deadline, VersionAdmission
from castlearq_desktop.core.contracts import ErrorCode, ReadinessCondition
from castlearq_desktop.core.contracts.interpreter import ValidationOutcome
from castlearq_desktop.core.interpreter_settings import InterpreterSettingsStore
from castlearq_desktop.core.lifecycle_manager import LifecycleManager, LifecycleState
from castlearq_desktop.core.lifecycle_observer import LifecycleObserver
from castlearq_desktop.core.readiness_qt_transport import (
    READINESS_PROBE_PATHS,
    QtReadinessChecker,
    QtReadinessTransport,
)
from castlearq_desktop.core.startup_controller import MonotonicClock, StartupController
from castlearq_desktop.core.startup_coordinator import CoreStartupCoordinator
from castlearq_desktop.core.subprocess_adapter import SubprocessProcessAdapter
from PySide6.QtNetwork import QNetworkReply

#: Opt-in gate: runtime integration tests run only when this equals "1".
RUNTIME_IT = os.environ.get("CASTLEARQ_RUNTIME_IT") == "1"

_SKIP_REASON = "set CASTLEARQ_RUNTIME_IT=1 to run runtime integration tests"


def _require_runtime_it() -> None:
    if not RUNTIME_IT:
        pytest.skip(_SKIP_REASON)


# --- purpose-built, short-lived test subprocesses (never CastleArq Core) -----
# Each script is inert: it neither performs network I/O nor touches user data,
# and it never spawns child processes.
_SCRIPT_EXIT_AFTER_DELAY = "import time; time.sleep(0.3)"
_SCRIPT_GRACEFUL = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\n"
    "time.sleep(30)\n"
)
_SCRIPT_IGNORE_TERM = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "time.sleep(30)\n"
)


def make_popen_factory(script, created=None):
    """Return a ``popen_factory`` that launches *script* instead of Core.

    The coordinator builds ``[python, "-m", "castlearq.main", "serve"]`` and the
    adapter forwards it to this factory; the factory intentionally substitutes a
    purpose-built test command (the passed argv is ignored). The returned
    ``Popen`` is owned and observed by the real adapter/manager. No production
    code is changed.
    """

    def factory(*args, **kwargs):
        proc = __import__("subprocess").Popen(
            [sys.executable, "-c", script], **kwargs
        )
        if created is not None:
            created.append(proc)
        return proc

    return factory


def emergency_reap(proc):
    """Bounded emergency cleanup: reap an owned test process, never leak it.

    This is harness cleanup only; it is not production lifecycle behavior.
    """
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
    except Exception:
        pass


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


def valid_outcome(python):
    return ValidationOutcome(code=None, python_version=(3, 12, 0),
                             distribution_found=True, package_found=True,
                             detail="ok")


# --- Qt event-loop helpers ---------------------------------------------------
def _app():
    """Reuse or create a QCoreApplication (no widgets are constructed here)."""
    return QCoreApplication.instance() or QCoreApplication(sys.argv[:1])


def _pump(app, seconds):
    """Process Qt events for *seconds* of real time (bounded).

    Needed only where a real OS event (subprocess exit) or a real QTimer must be
    allowed to occur; ``LifecycleManager`` emits no Qt signal of its own.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _watchdog(loop, timeout_s):
    """Start a single-shot watchdog that guarantees ``loop.exec()`` returns."""
    timer = QTimer()
    timer.setSingleShot(True)
    timer.start(int(timeout_s * 1000))
    timer.timeout.connect(loop.quit)
    return timer


# --- pending network fake (used to keep coordinator readiness inert) ---------
class _Sig:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _PendingReply:
    """A network reply that never completes (so readiness stays pending)."""

    def __init__(self):
        self.finished = _Sig()
        self._error = QNetworkReply.NetworkError.NoError
        self._status = None
        self.aborted = False

    def error(self):
        return self._error

    def attribute(self, attr):
        return None

    def abort(self):
        self.aborted = True  # no finished emission: stays cancelled

    def deleteLater(self):
        pass


class PendingNAM:
    """A QNetworkAccessManager stand-in whose replies never finish."""

    def __init__(self):
        self.requests = []

    def get(self, request):
        reply = _PendingReply()
        self.requests.append((request.url().toString(), reply))
        return reply


# --- loopback-only HTTP test server ------------------------------------------
class _ReadinessServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, responses, stop_event):
        super().__init__(addr, _ReadinessHandler)
        self.responses = responses
        self.stop_event = stop_event
        self.slow_paths = {}
        self.seen_paths = []


class _ReadinessHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence stderr; keep output clean
        pass

    def do_GET(self):
        server = self.server
        server.seen_paths.append(self.path)
        delay = server.slow_paths.get(self.path)
        if delay:
            waited = 0.0
            while waited < delay:
                if server.stop_event.wait(0.05):
                    return  # released during teardown
                waited += 0.05
        status = server.responses.get(self.path, 404)
        try:
            self.send_response(status)
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass


def start_server(responses, slow_paths=None):
    """Start a loopback-only server on an ephemeral port; return its handles."""
    stop_event = Event()
    server = _ReadinessServer(("127.0.0.1", 0), responses, stop_event)
    server.slow_paths = slow_paths or {}
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                    daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, thread, stop_event, port


def stop_server(server, thread, stop_event):
    """Bounded, verified server + serving-thread shutdown."""
    stop_event.set()
    try:
        server.shutdown()
    except Exception:
        pass
    thread.join(timeout=2.0)
    try:
        server.server_close()
    except Exception:
        pass


# =============================================================================
# Shared builders and bounded wait helpers for the runtime cases below.
# =============================================================================

#: Fictional absolute interpreter path (never executed: validation is faked).
ABS_PYTHON = "/abs/env/bin/python"

#: Deterministic 200 responses for the three approved endpoints (DEC-B).
_TWO_HUNDRED_OK = {path: 200 for path in READINESS_PROBE_PATHS}


def _coordinator_for(script=None, *, created=None,
                     api_contract=VersionAdmission.NOT_EVALUATED, qt_nam=None):
    """Build a production-shaped coordinator with test-only substitutions.

    Real components: ``InterpreterSettingsStore``, ``MonotonicClock``,
    ``QTimer`` as the real Qt timer factory, and the real
    ``SubprocessProcessAdapter`` (wired through the coordinator's counting
    adapter).  Test-only substitutions: an in-memory settings double, a faked
    validation outcome, a purpose-built ``[sys.executable -c script]``
    subprocess factory, and optionally an injected ``QNetworkAccessManager``.
    The admission value defaults to the production fail-closed default
    (DEC-A).  Nothing here contacts port 8000 unless readiness is started.
    """
    settings = InterpreterSettingsStore(FakeSettings())
    settings.save(ABS_PYTHON)
    popen_factory = (make_popen_factory(script, created)
                     if script is not None else None)
    return CoreStartupCoordinator(
        settings=settings,
        clock=MonotonicClock(),
        popen_factory=popen_factory,
        validator=valid_outcome,
        api_contract=api_contract,
        timer_factory=QTimer,
        qt_nam=qt_nam,
    )


def _wait_terminal(app, emissions, timeout_s):
    """Bounded, event-driven wait until *emissions* receives its first item.

    Returns True only if an emission actually arrived; the watchdog merely
    guarantees ``loop.exec()`` returns.  A watchdog expiry is never reported
    as a production outcome.
    """
    if emissions:
        return True
    loop = QEventLoop()
    watchdog = _watchdog(loop, timeout_s)
    poll = QTimer()
    poll.setInterval(20)
    poll.timeout.connect(lambda: loop.quit() if emissions else None)
    poll.start()
    loop.exec()
    poll.stop()
    watchdog.stop()
    return bool(emissions)


def _wait_state(app, predicate, timeout_s):
    """Bounded event pumping until *predicate* holds (state-driven cases).

    ``LifecycleManager`` emits no Qt signal of its own, so state transitions
    observed from the real observer can only be sampled by pumping the loop.
    """
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


# =============================================================================
# TC-1..TC-3 — real QtReadinessTransport + real QNetworkAccessManager against
# the loopback-only HTTP test server (ephemeral port).
# =============================================================================


def test_tc1_real_transport_admitted_readiness_completes():
    """TC-1: all three endpoints answer 200 → exactly one ready terminal.

    Uses the actual ``QtReadinessTransport`` and a real
    ``QNetworkAccessManager`` (``nam=None`` builds one internally).  The
    ``ADMITTED`` admission value is *test-only*: it exercises the transport's
    success path and does not approve or alter DEC-A for production.
    """
    _require_runtime_it()
    app = _app()
    server, thread, stop_event, port = start_server(dict(_TWO_HUNDRED_OK))
    transport = None
    results, errors = [], []
    try:
        clock = MonotonicClock()
        transport = QtReadinessTransport(
            f"http://127.0.0.1:{port}",
            child_observer=lambda: True,
            api_contract=VersionAdmission.ADMITTED,  # test-only, not DEC-A
            clock=clock,
            timer_factory=QTimer,
        )
        checker = QtReadinessChecker(transport)
        checker.result.connect(results.append)
        checker.error.connect(errors.append)
        loop = QEventLoop()
        watchdog = _watchdog(loop, 8.0)
        checker.result.connect(loop.quit)
        checker.start(now=clock.monotonic(),
                      deadline=Deadline.starting_now(clock.monotonic()))
        loop.exec()
        watchdog.stop()
        assert errors == []
        assert len(results) == 1, "exactly one terminal outcome"
        assert results[0].ready is True
        assert results[0].deadline_expired is False
        # All three approved endpoints were reached; concurrent requests
        # do not guarantee server-side arrival order.
        assert len(server.seen_paths) >= 3
        assert set(server.seen_paths[:3]) == set(READINESS_PROBE_PATHS)
        # Cancelling a finished operation emits nothing further.
        checker.stop()
        _pump(app, 0.1)
        assert len(results) == 1
    finally:
        if transport is not None:
            transport.stop()
        stop_server(server, thread, stop_event)


def test_tc2_fail_closed_admission_despite_successful_http():
    """TC-2: DEC-A stays fail-closed even with HTTP 200 on every endpoint.

    Transport success (requests actually served) is asserted separately from
    the final readiness decision, which must remain unsuccessful while the
    admission state is the production default ``NOT_EVALUATED``.
    """
    _require_runtime_it()
    app = _app()
    server, thread, stop_event, port = start_server(dict(_TWO_HUNDRED_OK))
    transport = None
    results, probes = [], []
    try:
        clock = MonotonicClock()
        transport = QtReadinessTransport(
            f"http://127.0.0.1:{port}",
            child_observer=lambda: True,
            api_contract=VersionAdmission.NOT_EVALUATED,  # production default
            clock=clock,
            timer_factory=QTimer,
        )
        checker = QtReadinessChecker(transport)
        checker.result.connect(results.append)
        transport.progress.connect(probes.append)
        loop = QEventLoop()
        watchdog = _watchdog(loop, 8.0)
        poll = QTimer()
        poll.setInterval(20)
        poll.timeout.connect(lambda: loop.quit() if probes else None)
        poll.start()
        checker.start(now=clock.monotonic(),
                      deadline=Deadline.starting_now(clock.monotonic()))
        loop.exec()
        poll.stop()
        watchdog.stop()
        assert probes, "at least one full pass must complete"
        # Transport success: one complete pass served all three endpoints.
        # The three requests use independent connections, so server-side
        # arrival order is not guaranteed; assert membership of a full pass,
        # order-independently (no invented count guarantee beyond one pass).
        assert sorted(server.seen_paths[:3]) == sorted(READINESS_PROBE_PATHS)
        # Final decision: fail-closed on the admission condition only.
        assert probes[0].ready is False
        assert {f.condition for f in probes[0].outcome.failures} == \
            {ReadinessCondition.API_CONTRACT}
        # Cancel instead of consuming the full 20 s budget; the terminal
        # result of the cancelled operation is non-ready as well.
        checker.stop()
        assert len(results) == 1
        assert results[0].ready is False
    finally:
        if transport is not None:
            transport.stop()
        stop_server(server, thread, stop_event)


def test_tc3_http_failure_is_not_a_transport_failure():
    """TC-3: HTTP 503 on /health maps to the approved stage code.

    A protocol failure (503, a real HTTP response) must stay distinguishable
    from a transport failure: the classified code is the health-stage code,
    not a transport classification and not an invented one.
    """
    _require_runtime_it()
    app = _app()
    responses = dict(_TWO_HUNDRED_OK)
    responses["/health"] = 503
    server, thread, stop_event, port = start_server(responses)
    transport = None
    probes, results = [], []
    try:
        clock = MonotonicClock()
        transport = QtReadinessTransport(
            f"http://127.0.0.1:{port}",
            child_observer=lambda: True,
            api_contract=VersionAdmission.ADMITTED,  # isolate HTTP semantics
            clock=clock,
            timer_factory=QTimer,
        )
        checker = QtReadinessChecker(transport)
        checker.result.connect(results.append)
        transport.progress.connect(probes.append)
        loop = QEventLoop()
        watchdog = _watchdog(loop, 8.0)
        poll = QTimer()
        poll.setInterval(20)
        poll.timeout.connect(lambda: loop.quit() if probes else None)
        poll.start()
        checker.start(now=clock.monotonic(),
                      deadline=Deadline.starting_now(clock.monotonic()))
        loop.exec()
        poll.stop()
        watchdog.stop()
        assert probes, "at least one full pass must complete"
        assert probes[0].ready is False
        # Approved stage code for the failing endpoint (HTTP, not transport).
        assert probes[0].error_code is ErrorCode.HEALTH_CHECK_FAILED
        assert {f.condition for f in probes[0].outcome.failures} == \
            {ReadinessCondition.HEALTH}
        # The other two endpoints still succeeded (they were served).
        assert "/v1/models" in server.seen_paths
        assert "/v1/artifacts" in server.seen_paths
        # Bounded outcome: cancel; never a ready terminal from a 503 pass.
        checker.stop()
        assert len(results) == 1
        assert results[0].ready is False
    finally:
        if transport is not None:
            transport.stop()
        stop_server(server, thread, stop_event)


# =============================================================================
# TC-4..TC-8 — real subprocess + real lifecycle observer + real controller.
# No readiness HTTP is started in these cases, so port 8000 is never touched.
# =============================================================================


def test_tc4_real_subprocess_observed_exit():
    """TC-4: the real adapter/manager/observer confirm a real child's exit.

    The purpose-built child exits by itself after 0.3 s; the 100 ms observer
    timer must observe the exit and drive the manager to UNEXPECTED_EXIT
    without any signal being sent by the test.
    """
    _require_runtime_it()
    app = _app()
    created = []
    coord = _coordinator_for(_SCRIPT_EXIT_AFTER_DELAY, created=created)
    try:
        result = coord.launch()
        assert result.status in ("ok", "pending")
        assert coord.observer is not None, "observer is created for follow-up"
        assert _wait_state(
            app,
            lambda: coord.manager.state is LifecycleState.UNEXPECTED_EXIT,
            6.0,
        ), f"observer must confirm exit, state={coord.manager.state}"
        # The owned child is reaped (no zombie) and no signal was needed.
        assert created[0].poll() is not None
    finally:
        for proc in created:
            emergency_reap(proc)


def test_tc5_graceful_shutdown_emits_single_stopped_terminal():
    """TC-5: SIGTERM-exiting child → exactly one STOPPED terminal.

    Shutdown is requested through the real ``StartupController``; the real
    observer confirms the exit; the event loop is never blocked by a
    synchronous wait in the lifecycle path.
    """
    _require_runtime_it()
    app = _app()
    created = []
    coord = _coordinator_for(_SCRIPT_GRACEFUL, created=created)
    controller = StartupController(coord)  # real QTimer tick + deadline
    terminals = []
    controller.shutdown_finished.connect(terminals.append)
    try:
        result = coord.launch()
        assert result.status in ("ok", "pending")
        assert coord.observer is not None
        t0 = time.monotonic()
        controller.request_shutdown()  # must return immediately (non-blocking)
        assert (time.monotonic() - t0) < 1.0, "request_shutdown must not block"
        assert _wait_terminal(app, terminals, 8.0), \
            "a terminal shutdown outcome must arrive"
        assert terminals == [LifecycleState.STOPPED]
        # Late observer activity after the terminal result changes nothing.
        _pump(app, 0.5)
        assert terminals == [LifecycleState.STOPPED]
        # A repeated request after the terminal outcome is inert.
        controller.request_shutdown()
        _pump(app, 0.1)
        assert terminals == [LifecycleState.STOPPED]
        # The child is confirmed gone (harness verification, not production).
        assert created[0].poll() is not None
    finally:
        for proc in created:
            emergency_reap(proc)


def test_tc6_escalation_confirms_exit_of_signal_ignoring_child():
    """TC-6: a child that ignores SIGTERM is escalated by the real manager.

    Escalation follows the existing manager policy (graceful window, then
    kill), driven by the real observer; the controller reports exactly one
    terminal result.  The 9 s harness watchdog is *not* the production 5 s
    deadline — it only guarantees the test returns; a watchdog expiry is
    never reported as a production shutdown outcome.
    """
    _require_runtime_it()
    app = _app()
    created = []
    coord = _coordinator_for(_SCRIPT_IGNORE_TERM, created=created)
    controller = StartupController(coord)
    terminals = []
    controller.shutdown_finished.connect(terminals.append)
    try:
        coord.launch()
        controller.request_shutdown()
        assert _wait_terminal(app, terminals, 9.0), \
            "escalated shutdown must reach a terminal outcome"
        assert len(terminals) == 1, "exactly one terminal notification"
        # Escalation (kill) confirms the exit well inside the 5 s deadline.
        assert terminals[0] is LifecycleState.STOPPED
        _pump(app, 0.3)
        assert len(terminals) == 1
        assert created[0].poll() is not None
    finally:
        for proc in created:
            emergency_reap(proc)


def test_tc8_repeated_requests_and_late_observation_are_inert():
    """TC-8: coalesced shutdown requests + late observer activity.

    Repeated shutdown requests while one is pending must not reset the
    deadline or duplicate the terminal result, and continued observer
    activity after the terminal outcome must change nothing.
    """
    _require_runtime_it()
    app = _app()
    created = []
    coord = _coordinator_for(_SCRIPT_GRACEFUL, created=created)
    controller = StartupController(coord)
    terminals = []
    controller.shutdown_finished.connect(terminals.append)
    try:
        coord.launch()
        controller.request_shutdown()
        controller.request_shutdown()  # coalesced: deadline not reset
        controller.request_shutdown()  # coalesced again
        assert _wait_terminal(app, terminals, 8.0)
        assert terminals == [LifecycleState.STOPPED]
        # The observer keeps ticking (100 ms) after the terminal outcome.
        _pump(app, 0.6)
        assert terminals == [LifecycleState.STOPPED]
    finally:
        for proc in created:
            emergency_reap(proc)


def test_tc7_cancel_readiness_during_shutdown(monkeypatch):
    """TC-7: shutdown cancels a pending readiness operation.

    The readiness request is a real ``QtReadinessTransport`` GET against the
    loopback server, but pointed at the ephemeral test port via a *test-local*
    monkeypatch of ``default_base_url`` (no production change; port 8000 is
    never touched).  The slow /health response keeps the operation pending;
    a heartbeat timer proves the event loop stays responsive meanwhile.
    Shutdown then cancels the checker: the cancelled completion is suppressed
    and the delayed late response can never produce a ready result.
    """
    _require_runtime_it()
    app = _app()
    server, thread, stop_event, port = start_server(
        dict(_TWO_HUNDRED_OK), slow_paths={"/health": 1.5})
    monkeypatch.setattr(
        "castlearq_desktop.core.readiness_client.default_base_url",
        lambda: f"http://127.0.0.1:{port}")
    created = []
    results, terminals, heartbeat = [], [], []
    # Production-default admission, but shutdown must win before any pass
    # could ever complete; test-only ADMITTED isolates the cancellation path.
    coord = _coordinator_for(_SCRIPT_GRACEFUL, created=created,
                             api_contract=VersionAdmission.ADMITTED)
    controller = StartupController(coord)
    controller.readiness_result.connect(results.append)
    controller.shutdown_finished.connect(terminals.append)
    hb = QTimer()
    hb.setInterval(50)
    hb.timeout.connect(lambda: heartbeat.append(1))
    try:
        controller.start()  # launch + begin async readiness (pending)
        hb.start()
        _pump(app, 0.3)     # slow /health keeps the request pending
        hb.stop()
        assert results == [], "nothing terminal while /health is slow"
        assert heartbeat, "event loop stays responsive while pending"
        controller.request_shutdown()  # cancels the pending checker
        assert _wait_terminal(app, terminals, 8.0)
        assert terminals == [LifecycleState.STOPPED]
        # Let the delayed 200 response arrive after the cancellation: it must
        # not resurrect the cancelled operation or emit a ready result.
        _pump(app, 1.8)
        assert results == [], "cancelled/late readiness emits nothing"
    finally:
        hb.stop()
        for proc in created:
            emergency_reap(proc)
        stop_server(server, thread, stop_event)


# --- explicitly deferred cases (not implemented here) ------------------------
#
# TC-9 (unconfirmed shutdown / unknown process status): a real OS subprocess
# cannot be safely forced into an artificial unknown-status state, and the
# existing unit suite already covers the unknown-status path with a fake
# handle (tests/test_startup_controller.py: confirmed vs unconfirmed shutdown,
# deadline expiry producing SHUTDOWN_FAILED, no termination after the
# deadline).  Not duplicated here; this real-subprocess suite does not
# cover the unknown-status path.
#
# Coordinator-level readiness against the production default base URL
# (127.0.0.1:8000): deferred per the port-8000 safety rule — safe use of
# port 8000 cannot be established without a runtime check, the port may be
# occupied, and no base-URL injection parameter may be added to production
# code.  TC-7 uses a test-local monkeypatch instead of touching that port.
