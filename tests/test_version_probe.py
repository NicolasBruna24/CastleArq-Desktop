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

"""Deterministic tests for the asynchronous installed-version probe.

These tests cover the worker, its Qt bridge, and the readiness hold/resume
integration. Processes, network, clocks and timers are faked; the only real
threads are the ones used to *prove* that the state update does not happen on
the worker. No CastleArq Core process is launched and no network is contacted.

Note: these tests do not demonstrate that the operating system can terminate
the process immediately while a probe is still running. Qt keeps the process
alive until in-flight work finishes; that is a documented lifecycle
consequence, not something asserted here.
"""

from __future__ import annotations

import gc
import json
import os
import threading
import time
import weakref
from types import SimpleNamespace
from typing import Optional

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QThread, QThreadPool, QTimer  # noqa: E402
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from castlearq_desktop.core.contracts import ErrorCode  # noqa: E402
from castlearq_desktop.core.contracts import VersionAdmission  # noqa: E402
from castlearq_desktop.core.contracts.interpreter import (  # noqa: E402
    ValidationOutcome,
)
from castlearq_desktop.core.interpreter_settings import (  # noqa: E402
    InterpreterSettingsStore,
)
from castlearq_desktop.core.startup_coordinator import (  # noqa: E402
    CoreStartupCoordinator,
)
from castlearq_desktop.core.version_admission import (  # noqa: E402
    MAXIMUM_VALIDATION_TIMEOUT_SECONDS,
)
from castlearq_desktop.core.version_probe import (  # noqa: E402
    VersionProbeBridge,
    VersionProbeRunnable,
)

ABS_PYTHON = "/abs/env/bin/python"

#: Test-only admitted version used by these doubles (not production).
PROBE_VERSION = "9.9.9-test"

#: Polling interval the transport arms between passes (DEC-E: 250 ms).
POLL_MS = 250

#: Global readiness deadline the transport arms once (DEC-E: 20 s).
DEADLINE_MS = 20000


def _health_body(version: str = PROBE_VERSION) -> bytes:
    return json.dumps({"version": version}).encode()


def valid_outcome(python: str = ABS_PYTHON) -> ValidationOutcome:
    return ValidationOutcome(code=None, python_version=(3, 12, 0),
                             distribution_found=True, package_found=True,
                             detail="ok")


def fake_execute(version: Optional[str] = PROBE_VERSION, *, returncode: int = 0):
    """Build an executor double standing in for ``subprocess.run``.

    Asserts the contract the production path relies on: no shell and the
    unchanged 5 s hard timeout.
    """
    def _execute(cmd, **kwargs):
        assert kwargs.get("shell") is False, "the probe must never use a shell"
        assert kwargs.get("timeout") == MAXIMUM_VALIDATION_TIMEOUT_SECONDS
        if version is None:
            return SimpleNamespace(returncode=returncode, stdout="")
        return SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps({"distribution_version": version}))
    return _execute


def raising_execute(exc: Exception):
    def _execute(cmd, **kwargs):
        raise exc
    return _execute


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
    def __init__(self, registry: Optional[list] = None) -> None:
        self.timeout = FakeSignal()
        self.interval = None
        self._active = False
        self.stopped = False
        self._registry = registry

    def setInterval(self, ms) -> None:
        self.interval = ms

    def setSingleShot(self, single) -> None:
        pass

    def isActive(self) -> bool:
        return self._active

    def start(self) -> None:
        self._active = True
        if self._registry is not None:
            self._registry.append(self)

    def stop(self) -> None:
        self._active = False
        self.stopped = True

    def fire(self) -> None:
        self.timeout.emit()


class FakePopen:
    def __init__(self, exit_code=None) -> None:
        self._exit_code = exit_code
        self.terminates = 0
        self.kills = 0

    def poll(self):
        return self._exit_code

    def is_alive(self) -> bool:
        return self._exit_code is None

    def terminate(self) -> None:
        self.terminates += 1

    def kill(self) -> None:
        self.kills += 1


class PopenFactory:
    def __init__(self, popen) -> None:
        self.popen = popen
        self.calls: list = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.popen


class FakeReply:
    """Minimal ``QNetworkReply`` double for the Qt readiness transport."""

    def __init__(self) -> None:
        self.finished = FakeSignal()
        self._error = QNetworkReply.NetworkError.NoError
        self._status = None
        self._body = b"{}"
        self._declared = None
        self.aborted = False

    def error(self):
        return self._error

    def attribute(self, attr):
        if attr == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self._status
        return None

    def readAll(self):
        return self._body

    def header(self, _kind):
        return self._declared

    def abort(self) -> None:
        self.aborted = True
        self._error = QNetworkReply.NetworkError.OperationCanceledError
        self.finished.emit()

    def deleteLater(self) -> None:
        pass

    def succeed(self, status, body=b"{}") -> None:
        self._error = QNetworkReply.NetworkError.NoError
        self._status = status
        self._body = body
        self.finished.emit()


class FakeNAM:
    def __init__(self) -> None:
        self.requests: list = []

    def get(self, request):
        reply = FakeReply()
        self.requests.append((request.url().toString(), reply))
        return reply


class FakePool:
    """Deterministic stand-in for a Qt worker pool.

    Queues runnables instead of running them, so a test decides exactly when
    the probe "completes" via :meth:`drain`.
    """

    def __init__(self) -> None:
        self.queued: list = []

    def start(self, runnable) -> None:
        self.queued.append(runnable)

    def drain(self) -> None:
        queued, self.queued = self.queued, []
        for runnable in queued:
            runnable.run()


@pytest.fixture()
def qt_app():
    return QApplication.instance() or QApplication([])


def make(*, pool=None, executor=None, provider=None,
         allow=(PROBE_VERSION,), clock=None, nam=None, registry=None):
    """Build a production-shaped coordinator wired to deterministic doubles."""
    settings = InterpreterSettingsStore(FakeSettings())
    settings.save(ABS_PYTHON)
    clock = clock if clock is not None else FakeClock()
    nam = nam if nam is not None else FakeNAM()
    registry = registry if registry is not None else []
    coordinator = CoreStartupCoordinator(
        settings=settings,
        clock=clock,
        popen_factory=PopenFactory(FakePopen()),
        timer_factory=lambda: FakeTimer(registry),
        validator=lambda python: valid_outcome(python),
        api_contract=VersionAdmission.ADMITTED,
        qt_nam=nam,
        probe_pool=pool,
        version_executor=executor,
        installed_version_provider=provider,
        supported_versions=frozenset(allow),
    )
    return SimpleNamespace(coord=coordinator, clock=clock, nam=nam,
                           registry=registry, pool=pool)


def complete_http(b) -> None:
    """Deliver a 2xx reply for each of the three approved endpoints."""
    assert len(b.nam.requests) == 3
    for _, reply in b.nam.requests:
        reply.succeed(200, _health_body())


def poll_timers(b):
    return [t for t in b.registry if t.interval == POLL_MS]


def deadline_timer(b):
    found = [t for t in b.registry if t.interval == DEADLINE_MS]
    assert len(found) == 1, "exactly one global deadline timer is expected"
    return found[0]


# ---------------------------------------------------------------------------
# N-9. Bridge and runnable lifecycle (unit level, no readiness involved)
# ---------------------------------------------------------------------------

def test_n9_bridge_delivers_on_owning_thread(qt_app):
    """A worker emission is delivered on the thread that owns the bridge."""
    main_thread = qt_app.thread()
    assert main_thread is not None
    seen: dict = {}
    bridge = VersionProbeBridge(lambda payload: seen.setdefault(
        "slot_thread", QThread.currentThread()))
    assert bridge.thread() is main_thread

    started = threading.Event()

    def emit_from_worker():
        seen["worker_thread"] = QThread.currentThread()
        bridge.deliver((1, PROBE_VERSION))
        started.set()

    worker = threading.Thread(target=emit_from_worker)
    worker.start()
    assert started.wait(5.0), "the worker never emitted"
    worker.join(5.0)

    # Pump the event loop so the queued delivery is actually dispatched.
    limit = time.monotonic() + 5.0
    while "slot_thread" not in seen and time.monotonic() < limit:
        qt_app.processEvents()
        time.sleep(0.005)

    assert seen.get("slot_thread") is main_thread, (
        "the delivery must run on the bridge's owning thread")
    assert seen.get("worker_thread") is not main_thread


def test_n9_bridge_swallows_consumer_exception():
    """A failing consumer must not propagate out of the bridge."""
    def boom(_payload):
        raise RuntimeError("consumer failed")

    bridge = VersionProbeBridge(boom)
    # Same-thread emit -> direct dispatch, which must still not raise.
    bridge.deliver((1, PROBE_VERSION))


def test_n9_runnable_with_dead_bridge_is_inert():
    """A runnable whose bridge is gone must neither signal nor raise."""
    transient = QObject()
    ref = weakref.ref(transient)
    del transient
    gc.collect()
    assert ref() is None, "the stand-in bridge must be collectable"

    runnable = VersionProbeRunnable(ref, 1, ABS_PYTHON,
                                    executor=fake_execute())
    runnable.run()  # inert: no bridge to signal, no exception


def test_n9_runnable_resolves_none_on_executor_failure():
    """A broken probe resolves to None so admission can fail closed."""
    delivered: list = []
    bridge = VersionProbeBridge(delivered.append)
    runnable = VersionProbeRunnable(weakref.ref(bridge), 7, ABS_PYTHON,
                                    executor=raising_execute(OSError("boom")))
    runnable.run()
    assert delivered == [(7, None)]


# ---------------------------------------------------------------------------
# N-8. Injected synchronous providers keep their existing behaviour
# ---------------------------------------------------------------------------

def test_n8_injected_provider_stays_synchronous():
    pool = FakePool()
    calls: list = []

    def provider():
        calls.append(1)
        return PROBE_VERSION

    b = make(pool=pool, executor=fake_execute(), provider=provider)
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)

    complete_http(b)

    # Resolved synchronously: no worker was ever queued and readiness
    # completed on the first pass.
    assert pool.queued == [], "an injected provider must not use the worker"
    assert calls == [1]
    assert len(results) == 1
    assert results[0].ready is True
    assert b.coord._version_cache_valid is True
    assert b.coord._cached_installed_version == PROBE_VERSION


def test_n8_no_pool_preserves_synchronous_query():
    """Without an injected pool the original synchronous query still runs."""
    calls: list = []

    def executor(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"distribution_version": PROBE_VERSION}))

    b = make(pool=None, executor=executor)
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)

    complete_http(b)

    assert len(calls) == 1, "the synchronous fallback must still query once"
    assert len(results) == 1
    assert results[0].ready is True


# ---------------------------------------------------------------------------
# N-2. Readiness holds while the version is pending
# ---------------------------------------------------------------------------

def test_n2_readiness_held_without_duplicate_requests():
    pool = FakePool()
    b = make(pool=pool, executor=fake_execute())
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    progress: list = []
    checker.result.connect(results.append)
    checker._transport.progress.connect(progress.append)

    complete_http(b)

    # Held: not finished, nothing emitted, and no request was repeated.
    assert results == [], "readiness must not finish while pending"
    assert progress == [], "a held pass must not emit a premature outcome"
    assert len(b.nam.requests) == 3, "retained responses must not be re-sent"
    assert b.coord._version_cache_valid is False
    assert checker._transport._held is True
    # Holding arms no polling timer: there is nothing to retry yet.
    assert poll_timers(b) == []
    # Exactly one probe was launched for this attempt.
    assert len(pool.queued) == 1


def test_n2_probe_launched_once_across_repeated_evaluations():
    pool = FakePool()
    b = make(pool=pool, executor=fake_execute())
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    complete_http(b)

    # Further evaluations of the same attempt must not queue more probes.
    checker._transport._evaluate_pass()
    checker._transport._evaluate_pass()
    assert len(pool.queued) == 1, "exactly one probe per startup attempt"


# ---------------------------------------------------------------------------
# N-3. Resuming reuses the retained responses and finishes exactly once
# ---------------------------------------------------------------------------

def test_n3_resume_completes_once_with_retained_responses():
    pool = FakePool()
    b = make(pool=pool, executor=fake_execute())
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)

    complete_http(b)
    assert results == []
    assert len(b.nam.requests) == 3

    pool.drain()  # the probe delivers PROBE_VERSION

    assert b.coord._version_cache_valid is True
    assert b.coord._cached_installed_version == PROBE_VERSION
    assert len(results) == 1, "the operation must finish exactly once"
    assert results[0].ready is True
    assert results[0].error_code is None
    assert len(b.nam.requests) == 3, "resuming must reuse retained responses"


# ---------------------------------------------------------------------------
# N-7. A probe that resolves to None stays fail-closed
# ---------------------------------------------------------------------------

def test_n7_failed_probe_stays_fail_closed():
    pool = FakePool()
    b = make(pool=pool, executor=fake_execute(None))
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)

    complete_http(b)
    pool.drain()

    # Resolved, but to "absent": never an admitted version.
    assert b.coord._version_cache_valid is True
    assert b.coord._cached_installed_version is None
    assert results == [], "an absent version must never be reported as ready"
    # The pass is re-armed through the normal polling path, fail-closed.
    armed = poll_timers(b)
    assert len(armed) == 1

    b.clock.advance(0.25)
    armed[0].fire()
    assert not any(r.ready for r in results)


def test_n7_nonzero_exit_stays_fail_closed():
    pool = FakePool()
    b = make(pool=pool, executor=fake_execute(None, returncode=1))
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)

    complete_http(b)
    pool.drain()

    assert b.coord._cached_installed_version is None
    assert b.coord._version_cache_valid is True
    assert not any(r.ready for r in results)


def test_n7_unconfigured_interpreter_resolves_absent_without_probe():
    """No interpreter configured: fail closed instead of holding forever."""
    pool = FakePool()
    settings = InterpreterSettingsStore(FakeSettings())  # nothing saved
    coordinator = CoreStartupCoordinator(
        settings=settings,
        clock=FakeClock(),
        popen_factory=PopenFactory(FakePopen()),
        timer_factory=lambda: FakeTimer([]),
        validator=lambda python: valid_outcome(python),
        api_contract=VersionAdmission.ADMITTED,
        qt_nam=FakeNAM(),
        probe_pool=pool,
        version_executor=fake_execute(),
        supported_versions=frozenset({PROBE_VERSION}),
    )
    assert coordinator._version_pending() is False
    assert coordinator._version_cache_valid is True
    assert coordinator._cached_installed_version is None
    assert pool.queued == [], "nothing may be probed without an interpreter"


# ---------------------------------------------------------------------------
# N-5. Stale results, cancellation and shutdown
# ---------------------------------------------------------------------------

def test_n5_invalidate_discards_pending_result():
    pool = FakePool()
    b = make(pool=pool, executor=fake_execute())
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)

    complete_http(b)
    assert len(pool.queued) == 1

    b.coord.invalidate_version_probe()
    pool.drain()

    assert b.coord._version_cache_valid is False, (
        "a stale delivery must not populate the cache")
    assert b.coord._cached_installed_version is None
    assert results == []


def test_n5_new_attempt_supersedes_pending_probe():
    """A second launch must retire the probe still in flight."""
    pool = FakePool()
    b = make(pool=pool, executor=fake_execute("0.0.1-stale"))
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)
    complete_http(b)

    first_generation = b.coord._version_generation
    assert len(pool.queued) == 1

    # Simulate a new startup attempt: the manager must permit it first.
    b.coord.manager.stop()
    b.coord.manager.discard_terminated_handle()
    b.coord.launch()

    assert b.coord._version_generation != first_generation
    pool.drain()  # the stale probe finally delivers

    assert b.coord._cached_installed_version is None, (
        "a superseded probe must not populate the cache")
    assert not any(r.ready for r in results)


def test_n5_stop_before_delivery_never_becomes_ready():
    pool = FakePool()
    b = make(pool=pool, executor=fake_execute())
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)

    complete_http(b)
    checker.stop()  # cancellation / shutdown path

    pool.drain()    # a late delivery arrives afterwards

    assert not any(r.ready for r in results), (
        "a late delivery must never turn a stopped operation into READY")
    # The transport is finished, so resume() is inert even though the
    # delivery did update the cache.
    assert b.coord._version_cache_valid is True
    assert b.coord._cached_installed_version == PROBE_VERSION


# ---------------------------------------------------------------------------
# N-6. The global deadline prevails while the probe is pending
# ---------------------------------------------------------------------------

def test_n6_global_deadline_prevails_while_pending():
    pool = FakePool()
    clock = FakeClock(0.0)
    b = make(pool=pool, executor=fake_execute(), clock=clock)
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)

    complete_http(b)
    assert results == []

    # The global deadline fires while the probe is still in flight.
    clock.advance(20.0)
    deadline_timer(b).fire()

    assert len(results) == 1, "the deadline must finalize the operation"
    assert results[0].ready is False
    assert results[0].error_code is ErrorCode.READINESS_TIMEOUT
    assert results[0].deadline_expired is True

    # A late delivery cannot reopen a finished operation.
    pool.drain()
    assert len(results) == 1
    assert not any(r.ready for r in results)


# ---------------------------------------------------------------------------
# N-1. The GUI thread is not blocked while the probe runs
# ---------------------------------------------------------------------------

def test_n1_gui_not_blocked_while_probe_pending(qt_app):
    """The event loop keeps processing while the probe runs on a worker."""
    started = threading.Event()
    release = threading.Event()

    def slow_execute(cmd, **kwargs):
        started.set()
        release.wait(10.0)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"distribution_version": PROBE_VERSION}))

    b = make(pool=QThreadPool.globalInstance(), executor=slow_execute)
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)

    # The pass is held once the responses arrive, which is also what launches
    # the probe: the probe only starts when DEC-A actually needs the version.
    complete_http(b)
    assert started.wait(10.0), "the probe never started on a worker"

    # An independent heartbeat must keep firing: proof the GUI thread is free.
    beats: list = []
    heartbeat = QTimer()
    heartbeat.setInterval(5)
    heartbeat.timeout.connect(lambda: beats.append(1))
    heartbeat.start()
    limit = time.monotonic() + 2.0
    while len(beats) < 5 and time.monotonic() < limit:
        qt_app.processEvents()
        time.sleep(0.001)
    heartbeat.stop()

    assert len(beats) >= 5, (
        "the event loop was blocked while the probe was running")

    release.set()
    limit = time.monotonic() + 10.0
    while not results and time.monotonic() < limit:
        qt_app.processEvents()
        time.sleep(0.005)

    assert len(results) == 1
    assert results[0].ready is True


# ---------------------------------------------------------------------------
# N-4. Coordinator state is updated on the Qt thread, not the worker
# ---------------------------------------------------------------------------

def test_n4_state_updated_on_qt_thread(qt_app):
    main_thread = qt_app.thread()
    seen: dict = {}
    release = threading.Event()

    def slow_execute(cmd, **kwargs):
        seen["worker"] = QThread.currentThread()
        release.wait(10.0)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"distribution_version": PROBE_VERSION}))

    b = make(pool=QThreadPool.globalInstance(), executor=slow_execute)
    b.coord.launch()
    checker = b.coord.check_readiness_async(now=0.0)
    results: list = []
    checker.result.connect(results.append)

    complete_http(b)

    # The bridge exists now; wrap its consumer to observe the delivery thread.
    bridge = b.coord._probe_bridge
    assert bridge is not None
    original = bridge._on_resolved

    def spy(payload):
        seen["slot"] = QThread.currentThread()
        original(payload)

    bridge._on_resolved = spy

    release.set()
    limit = time.monotonic() + 10.0
    while not results and time.monotonic() < limit:
        qt_app.processEvents()
        time.sleep(0.005)

    assert seen.get("worker") is not main_thread, (
        "the probe must run off the Qt thread")
    assert seen.get("slot") is main_thread, (
        "the coordinator state must be updated on the Qt thread")
    assert len(results) == 1
    assert results[0].ready is True
