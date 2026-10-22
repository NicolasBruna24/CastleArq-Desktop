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

"""WP2B tests: lifecycle manager with fake adapter and fake clock."""

from __future__ import annotations

import logging
import pytest
from unittest.mock import patch

from castlearq_desktop.core.contracts import (
    ARTIFACTS_PATH, HEALTH_PATH, MODELS_PATH)
from castlearq_desktop.core.contracts.errors import APPROVED_ERROR_CODES
from castlearq_desktop.core.contracts.errors import ErrorCode
from castlearq_desktop.core.contracts.shutdown import ShutdownPhase
from castlearq_desktop.core.lifecycle_manager import LifecycleManager
from castlearq_desktop.core.lifecycle_manager import LifecycleState
from castlearq_desktop.core.lifecycle_manager import ProcessOwnershipUncertainError
from castlearq_desktop.core.lifecycle_manager import ProcessSpawnError
from castlearq_desktop.core.lifecycle_manager import build_launch_args

ABS_PYTHON = "/abs/env/bin/python"


class FakeHandle:
    def __init__(self, *, alive=True, exit_code=None):
        self._alive = alive
        self._exit_code = exit_code
        self.terminates = 0
        self.kills = 0

    def is_alive(self):
        return self._alive

    def poll(self):
        return None if self._alive else self._exit_code

    def terminate(self):
        self.terminates += 1

    def kill(self):
        self.kills += 1

    def exit(self, code=0):
        self._alive = False
        self._exit_code = code


class FakeAdapter:
    def __init__(self, behavior="ok"):
        self.behavior = behavior
        self.calls = []
        self.handle = FakeHandle()
        self.partial = FakeHandle()

    def spawn(self, argv, *, shell):
        self.calls.append({"argv": list(argv), "shell": shell})
        if self.behavior == "ok":
            return self.handle
        if self.behavior == "immediate-exit":
            self.handle.exit(3)
            return self.handle
        if self.behavior == "definite-fail":
            raise ProcessSpawnError("nope")
        if self.behavior == "uncertain-no-handle":
            raise ProcessOwnershipUncertainError("maybe")
        if self.behavior == "uncertain-partial":
            raise ProcessOwnershipUncertainError("maybe",
                                                partial_handle=self.partial)
        if self.behavior == "none-handle":
            return None
        raise AssertionError("unknown")


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def advance(self, dt):
        self.now += dt


def make(behavior="ok"):
    adapter = FakeAdapter(behavior)
    clock = FakeClock()
    mgr = LifecycleManager(adapter=adapter, clock=clock)
    return mgr, adapter, clock


def test_eight_states_exact():
    assert {s.value for s in LifecycleState} == {
        "not_started", "starting", "running", "stopping", "stopped",
        "start_failed", "unexpected_exit", "shutdown_failed"}


def test_launch_args_exact():
    assert build_launch_args(ABS_PYTHON) == [
        ABS_PYTHON, "-m", "castlearq.main", "serve"]


@pytest.mark.parametrize("bad", ["", "   ", "relative/python", 123, None])
def test_bad_paths_rejected(bad):
    with pytest.raises(ValueError):
        build_launch_args(bad)


def test_start_ok_single_spawn_shell_false():
    mgr, adapter, _ = make()
    res = mgr.start(ABS_PYTHON)
    assert res.status == "ok" and mgr.state is LifecycleState.RUNNING
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["shell"] is False
    assert adapter.calls[0]["argv"] == [ABS_PYTHON, "-m", "castlearq.main",
                                        "serve"]


def test_start_bad_path_uses_interpreter_invalid():
    mgr, adapter, _ = make()
    res = mgr.start("relative/python")
    assert res.status == "failed"
    assert res.error.code == ErrorCode.INTERPRETER_INVALID
    assert adapter.calls == []


def test_duplicate_start_refused():
    mgr, adapter, _ = make()
    mgr.start(ABS_PYTHON)
    res = mgr.start(ABS_PYTHON)
    assert res.status == "refused"
    assert len(adapter.calls) == 1


def test_start_during_stopping_suppressed():
    mgr, adapter, _ = make()
    mgr.start(ABS_PYTHON)
    mgr.stop()
    res = mgr.start(ABS_PYTHON)
    assert res.status == "refused" and res.startup_suppressed is True
    assert len(adapter.calls) == 1


def test_definite_spawn_failure_retry_ok():
    mgr, adapter, _ = make("definite-fail")
    res = mgr.start(ABS_PYTHON)
    assert res.status == "failed"
    assert res.error.code == ErrorCode.PROCESS_START_FAILED
    assert mgr.ownership_unresolved is False
    adapter.behavior = "ok"
    adapter.handle = FakeHandle()
    assert mgr.start(ABS_PYTHON).status == "ok"


def test_uncertain_no_handle_sticky():
    mgr, adapter, _ = make("uncertain-no-handle")
    res = mgr.start(ABS_PYTHON)
    assert res.status == "failed"
    assert mgr.ownership_unresolved is True
    assert mgr.start(ABS_PYTHON).status == "refused"
    assert len(adapter.calls) == 1
    stop = mgr.stop()
    assert stop.status == "failed"
    assert stop.error.code == ErrorCode.SHUTDOWN_FAILED
    mgr.observe()
    assert mgr.ownership_unresolved is True


def test_uncertain_partial_handle_adopted():
    mgr, adapter, _ = make("uncertain-partial")
    res = mgr.start(ABS_PYTHON)
    assert res.status == "ok"
    assert mgr.has_owned_handle is True


def test_immediate_exit_error_none():
    mgr, adapter, _ = make("immediate-exit")
    res = mgr.start(ABS_PYTHON)
    assert res.status == "failed"
    assert res.state is LifecycleState.UNEXPECTED_EXIT
    assert res.error is None
    assert mgr.start(ABS_PYTHON).status == "refused"
    assert mgr.discard_terminated_handle() is True
    adapter.behavior = "ok"
    adapter.handle = FakeHandle()
    assert mgr.start(ABS_PYTHON).status == "ok"


def test_observe_unexpected_exit_running():
    mgr, adapter, _ = make()
    mgr.start(ABS_PYTHON)
    adapter.handle.exit(1)
    mgr.observe()
    assert mgr.state is LifecycleState.UNEXPECTED_EXIT


def test_stop_idempotent_no_handle():
    mgr, _, _ = make()
    res = mgr.stop()
    assert res.status == "ok" and mgr.state is LifecycleState.STOPPED


def test_stop_coalesces_no_restart():
    mgr, adapter, clock = make()
    mgr.start(ABS_PYTHON)
    assert mgr.stop().status == "pending"
    t0 = mgr._stop_requested_at
    assert mgr.stop().status == "coalesced"
    assert mgr._stop_requested_at == t0
    assert adapter.handle.terminates == 1


def test_graceful_escalation_and_budget():
    mgr, adapter, clock = make()
    mgr.start(ABS_PYTHON)
    mgr.stop()
    clock.advance(1.0)
    mgr.observe()
    assert mgr.shutdown_phase is ShutdownPhase.GRACEFUL
    assert adapter.handle.kills == 0
    clock.advance(1.0)
    mgr.observe()
    assert mgr.shutdown_phase is ShutdownPhase.FORCED
    assert adapter.handle.kills == 1
    clock.advance(2.999)
    mgr.observe()
    assert mgr.state is LifecycleState.STOPPING
    clock.advance(0.001)
    mgr.observe()
    assert mgr.state is LifecycleState.SHUTDOWN_FAILED
    assert adapter.handle.kills == 1


def test_no_late_kill_after_budget():
    mgr, adapter, clock = make()
    mgr.start(ABS_PYTHON)
    mgr.stop()
    clock.advance(6.0)
    mgr.observe()
    assert mgr.state is LifecycleState.SHUTDOWN_FAILED
    assert adapter.handle.kills == 0


def test_failed_retains_and_refuses():
    mgr, adapter, clock = make()
    mgr.start(ABS_PYTHON)
    mgr.stop()
    clock.advance(5.0)
    mgr.observe()
    assert mgr.has_owned_handle is True
    assert mgr.start(ABS_PYTHON).status == "refused"
    assert mgr.stop().status == "refused"


def test_late_exit_preserves_record():
    mgr, adapter, clock = make()
    mgr.start(ABS_PYTHON)
    mgr.stop()
    clock.advance(5.0)
    mgr.observe()
    assert mgr.shutdown_failed_reported is True
    adapter.handle.exit(0)
    mgr.observe()
    assert mgr.state is LifecycleState.STOPPED
    assert mgr.shutdown_failed_reported is True


def test_exit_within_budget_confirms():
    mgr, adapter, clock = make()
    mgr.start(ABS_PYTHON)
    mgr.stop()
    clock.advance(1.0)
    adapter.handle.exit(0)
    mgr.observe()
    assert mgr.state is LifecycleState.STOPPED
    assert mgr.shutdown_phase is ShutdownPhase.CONFIRMED


def test_error_vocabulary_unchanged():
    assert len(APPROVED_ERROR_CODES) == 9


class FailingStatusHandle(FakeHandle):
    """Both probes raise: status is unknown, not a confirmed exit."""

    def __init__(self):
        super().__init__(alive=True)
        self.poll_calls = 0
        self.alive_calls = 0

    def poll(self):
        self.poll_calls += 1
        raise RuntimeError("poll unavailable")

    def is_alive(self):
        self.alive_calls += 1
        raise RuntimeError("status unavailable")


class PollFailAliveHandle(FakeHandle):
    """poll() raises but is_alive() reports running."""

    def poll(self):
        raise RuntimeError("poll unavailable")


def test_unknown_status_not_treated_as_exit():
    mgr, adapter, _ = make()
    mgr.start(ABS_PYTHON)
    before = mgr.state
    adapter.handle = FailingStatusHandle()
    mgr._handle = adapter.handle
    mgr.observe()
    # Handle retained; no transition assuming safe resolution.
    assert mgr.has_owned_handle is True
    assert mgr.state is before
    assert mgr.state not in (LifecycleState.STOPPED,
                             LifecycleState.UNEXPECTED_EXIT)


def test_unknown_status_stop_does_not_signal():
    mgr, adapter, _ = make()
    mgr.start(ABS_PYTHON)
    failing = FailingStatusHandle()
    adapter.handle = failing
    mgr._handle = failing
    res = mgr.stop()
    assert res.status == "pending"
    assert res.error is None
    assert failing.terminates == 0
    assert mgr.has_owned_handle is True
    assert mgr.state is LifecycleState.RUNNING


def test_poll_failure_falls_back_to_is_alive():
    mgr, adapter, _ = make()
    mgr.start(ABS_PYTHON)
    probe = PollFailAliveHandle()
    adapter.handle = probe
    mgr._handle = probe
    mgr.observe()
    assert mgr.state is LifecycleState.RUNNING
    assert mgr.has_owned_handle is True


def test_unknown_status_during_stopping_never_signals():
    mgr, adapter, clock = make()
    mgr.start(ABS_PYTHON)
    mgr.stop()
    assert adapter.handle.terminates == 1
    failing = FailingStatusHandle()
    adapter.handle = failing
    mgr._handle = failing
    clock.advance(6.0)
    mgr.observe()
    assert failing.kills == 0
    assert mgr.state is LifecycleState.STOPPING
    assert mgr.shutdown_phase is ShutdownPhase.GRACEFUL
    assert mgr.has_owned_handle is True


def test_terminate_failure_consistent_record():
    mgr, adapter, clock = make()
    mgr.start(ABS_PYTHON)
    adapter.handle.terminate = _raising_terminate
    res = mgr.stop()
    assert res.status == "failed"
    assert res.error.code == ErrorCode.SHUTDOWN_FAILED
    assert mgr.state is LifecycleState.SHUTDOWN_FAILED
    assert mgr.shutdown_phase is ShutdownPhase.FAILED
    assert mgr.shutdown_failed_reported is True
    # Handle retained; new starts refused.
    assert mgr.has_owned_handle is True
    assert mgr.start(ABS_PYTHON).status == "refused"
    # Observation sends no further signal: terminate still exactly once.
    terminates_before = adapter.handle.terminates
    kills_before = adapter.handle.kills
    clock.advance(6.0)
    mgr.observe()
    assert mgr.state is LifecycleState.SHUTDOWN_FAILED
    assert adapter.handle.terminates == terminates_before
    assert adapter.handle.kills == kills_before


def _raising_terminate(self=None):
    raise RuntimeError("terminate failed")


class ScriptedStatusHandle(FakeHandle):
    """Deterministic scripted probe sequence with call counting."""

    def __init__(self, script):
        super().__init__(alive=True)
        self._script = list(script)
        self.probe_calls = 0

    def _next(self, kind):
        self.probe_calls += 1
        assert self._script, f"unexpected extra probe ({kind})"
        outcome = self._script.pop(0)
        assert outcome[0] == kind, outcome
        action = outcome[1]
        if isinstance(action, Exception):
            raise action
        return action

    def poll(self):
        return self._next("poll")

    def is_alive(self):
        return self._next("alive")


class CountingUnknownHandle(FakeHandle):
    """Always unknown; counts poll/is_alive invocations."""

    def __init__(self):
        super().__init__(alive=True)
        self.poll_calls = 0
        self.alive_calls = 0

    def poll(self):
        self.poll_calls += 1
        raise RuntimeError("poll unavailable")

    def is_alive(self):
        self.alive_calls += 1
        raise RuntimeError("status unavailable")


def _start_with_handle(mgr, adapter, handle):
    adapter.behavior = "ok"
    adapter.handle = handle
    return mgr.start(ABS_PYTHON)


def test_initial_alive_ok_running():
    mgr, adapter, _ = make()
    res = _start_with_handle(mgr, adapter, FakeHandle(alive=True))
    assert res.status == "ok"
    assert res.state is LifecycleState.RUNNING
    assert res.error is None
    assert mgr.state is LifecycleState.RUNNING
    assert mgr.has_owned_handle is True
    assert mgr.ownership_unresolved is False
    assert len(adapter.calls) == 1


def test_initial_unknown_pending_starting():
    mgr, adapter, _ = make()
    handle = CountingUnknownHandle()
    res = _start_with_handle(mgr, adapter, handle)
    assert res.status == "pending"
    assert res.state is LifecycleState.STARTING
    assert res.error is None
    assert mgr.state is LifecycleState.STARTING
    assert mgr.has_owned_handle is True
    assert mgr._handle is handle
    assert mgr.ownership_unresolved is False
    assert handle.terminates == 0
    assert handle.kills == 0


def test_initial_unknown_partial_handle_pending():
    mgr, adapter, _ = make("uncertain-partial")
    adapter.partial = CountingUnknownHandle()
    res = mgr.start(ABS_PYTHON)
    assert res.status == "pending"
    assert res.state is LifecycleState.STARTING
    assert res.error is None
    assert mgr.has_owned_handle is True
    assert mgr.ownership_unresolved is False


def test_continued_unknown_preserves_starting_no_signals():
    mgr, adapter, clock = make()
    handle = CountingUnknownHandle()
    _start_with_handle(mgr, adapter, handle)
    assert mgr.state is LifecycleState.STARTING
    clock.advance(10.0)
    mgr.observe()
    assert mgr.state is LifecycleState.STARTING
    assert mgr.has_owned_handle is True
    assert mgr._handle is handle
    assert handle.terminates == 0
    assert handle.kills == 0
    res = mgr.stop()
    assert res.status == "pending"
    assert res.error is None
    assert mgr.state is LifecycleState.STARTING
    assert handle.terminates == 0
    assert mgr.has_owned_handle is True


def test_confirmed_liveness_resolves_starting_to_running():
    mgr, adapter, _ = make()
    unknown = CountingUnknownHandle()
    _start_with_handle(mgr, adapter, unknown)
    assert mgr.state is LifecycleState.STARTING
    alive = FakeHandle(alive=True)
    mgr._handle = alive
    adapter.handle = alive
    mgr.observe()
    assert mgr.state is LifecycleState.RUNNING
    assert mgr.has_owned_handle is True
    assert alive.terminates == 0
    assert alive.kills == 0


def test_confirmed_exit_from_starting_unexpected_exit():
    mgr, adapter, _ = make()
    _start_with_handle(mgr, adapter, CountingUnknownHandle())
    assert mgr.state is LifecycleState.STARTING
    exited = FakeHandle(alive=True)
    exited.exit(7)
    mgr._handle = exited
    adapter.handle = exited
    mgr.observe()
    assert mgr.state is LifecycleState.UNEXPECTED_EXIT
    assert mgr.has_owned_handle is False
    assert mgr.discard_terminated_handle() is False


def test_duplicate_start_while_starting_refused():
    mgr, adapter, _ = make()
    _start_with_handle(mgr, adapter, CountingUnknownHandle())
    assert mgr.state is LifecycleState.STARTING
    res = mgr.start(ABS_PYTHON)
    assert res.status == "refused"
    assert res.error is None
    assert len(adapter.calls) == 1
    assert mgr.state is LifecycleState.STARTING
    assert mgr.has_owned_handle is True


def test_pending_distinct_from_unresolved_ownership():
    mgr, adapter, _ = make()
    res = _start_with_handle(mgr, adapter, CountingUnknownHandle())
    assert res.status == "pending"
    assert mgr.ownership_unresolved is False
    stuck, _, _ = make("uncertain-no-handle")
    failed = stuck.start(ABS_PYTHON)
    assert failed.status == "failed"
    assert stuck.ownership_unresolved is True
    assert stuck.start(ABS_PYTHON).status == "refused"
    assert mgr.start(ABS_PYTHON).status == "refused"
    assert mgr.ownership_unresolved is False


def test_observe_single_probe_per_call():
    mgr, adapter, _ = make()
    handle = ScriptedStatusHandle([("poll", None), ("alive", True)])
    _start_with_handle(mgr, adapter, handle)
    handle._script = [("poll", None), ("alive", True)]
    calls_before = handle.probe_calls
    mgr.observe()
    assert handle.probe_calls - calls_before == 2
    assert mgr.state is LifecycleState.RUNNING
    mgr2, adapter2, _ = make()
    unknown = CountingUnknownHandle()
    _start_with_handle(mgr2, adapter2, unknown)
    polls = unknown.poll_calls
    alives = unknown.alive_calls
    mgr2.observe()
    assert unknown.poll_calls - polls == 1
    assert unknown.alive_calls - alives == 1
    assert mgr2.state is LifecycleState.STARTING


def test_generic_spawn_exception_is_indeterminate():
    mgr, adapter, _ = make()
    adapter.behavior = "generic-fail"

    def _boom(argv, *, shell):
        raise RuntimeError("boom")

    adapter.spawn = _boom
    res = mgr.start(ABS_PYTHON)
    assert res.status == "failed"
    assert res.error.code == ErrorCode.PROCESS_START_FAILED
    # Ownership-safety invariant: retry is refused, not permitted.
    assert mgr.ownership_unresolved is True
    assert mgr.state is LifecycleState.START_FAILED
    assert mgr.start(ABS_PYTHON).status == "refused"
    assert mgr.stop().status == "failed"


def test_readiness_transport_module_has_logger():
    """El transporte de readiness expone un logger dedicado al diagnóstico."""
    pytest.importorskip("PySide6")
    import logging
    from castlearq_desktop.core import (
        readiness_qt_transport as readiness_qt_transport)
    assert isinstance(readiness_qt_transport._logger, logging.Logger)
    assert readiness_qt_transport._logger.name == (
        "castlearq_desktop.core.readiness_qt_transport")


def test_readiness_transport_has_instrumentation_attributes():
    """El transporte expone los puntos de instrumentación declarados."""
    pytest.importorskip("PySide6")
    from unittest.mock import MagicMock
    from castlearq_desktop.core import (
        readiness_qt_transport as readiness_qt_transport)
    QtReadinessTransport = readiness_qt_transport.QtReadinessTransport

    transport = QtReadinessTransport(
        "http://127.0.0.1:8000",
        child_observer=lambda: True,
        api_contract=MagicMock(),
        clock=MagicMock(),
        timer_factory=MagicMock(),
        nam=MagicMock(),
    )
    for attr in (
        "_request_start_times",
        "_request_durations",
        "_log_pass",
        "_issue_request",
        "_on_reply_finished",
        "_cleanup",
    ):
        assert hasattr(transport, attr), attr


def test_log_pass_records_duration_and_not_started(caplog):
    """_log_pass registra duración real, timeouts y peticiones no iniciadas.

    Se instancia la clase sin llamar a QObject.__init__ (parcheado) para no
    requerir PySide6 ni tráfico HTTP; solo se prueba la lógica de registro.
    """
    pytest.importorskip("PySide6")
    from castlearq_desktop.core import (
        readiness_qt_transport as readiness_qt_transport)
    QtReadinessTransport = readiness_qt_transport.QtReadinessTransport

    with patch.object(QtReadinessTransport, "__init__", lambda self, *a, **k: None):
        mgr = QtReadinessTransport.__new__(QtReadinessTransport)
        mgr._passes = 3
        mgr._alive_before = True
        mgr._pass_results = {
            HEALTH_PATH: ("ok", 200, None),
            MODELS_PATH: ("ok", 200, None),
            ARTIFACTS_PATH: ("timeout", None, None),
        }
        mgr._request_durations = {
            HEALTH_PATH: 0.041,
            MODELS_PATH: 0.063,
            ARTIFACTS_PATH: None,
        }
        mgr._deadline = None

        with caplog.at_level(
            logging.DEBUG,
            logger="castlearq_desktop.core.readiness_qt_transport",
        ):
            mgr._log_pass(
                now=100.0,
                alive_after=False,
                overrun=False,
                code=ErrorCode.API_PROBE_FAILED,
                ready=False,
            )

    assert "pass=3" in caplog.text
    assert "path=/health kind=ok status=200 duration_ms=41" in caplog.text
    assert "path=/v1/models kind=ok status=200 duration_ms=63" in caplog.text
    assert (
        "path=/v1/artifacts kind=timeout status=None duration_ms=None"
        in caplog.text
    )
    assert "child_before=True child_after=False" in caplog.text
    assert "overrun=False" in caplog.text
    assert "code=API_PROBE_FAILED" in caplog.text
    assert "ready=False" in caplog.text


def test_log_pass_marks_not_started_when_no_duration(caplog):
    """Una petición no iniciada (duración None) se reporta como not_started."""
    pytest.importorskip("PySide6")
    from castlearq_desktop.core import (
        readiness_qt_transport as readiness_qt_transport)
    QtReadinessTransport = readiness_qt_transport.QtReadinessTransport

    with patch.object(QtReadinessTransport, "__init__", lambda self, *a, **k: None):
        mgr = QtReadinessTransport.__new__(QtReadinessTransport)
        mgr._passes = 1
        mgr._alive_before = None
        mgr._pass_results = {
            HEALTH_PATH: ("ok", 200, None),
            MODELS_PATH: ("not_started", None, None),
            ARTIFACTS_PATH: ("transport_error", None, None),
        }
        mgr._request_durations = {
            HEALTH_PATH: 0.010,
            MODELS_PATH: None,
            ARTIFACTS_PATH: None,
        }
        mgr._deadline = None

        with caplog.at_level(
            logging.DEBUG,
            logger="castlearq_desktop.core.readiness_qt_transport",
        ):
            mgr._log_pass(
                now=5.0,
                alive_after=True,
                overrun=True,
                code=ErrorCode.READINESS_TIMEOUT,
                ready=False,
            )

    assert "pass=1" in caplog.text
    assert "path=/health kind=ok status=200 duration_ms=10" in caplog.text
    assert (
        "path=/v1/models kind=not_started status=None duration_ms=None"
        in caplog.text
    )
    assert (
        "path=/v1/artifacts kind=transport_error status=None duration_ms=None"
        in caplog.text
    )
    assert "overrun=True" in caplog.text
    assert "code=READINESS_TIMEOUT" in caplog.text
    assert "ready=False" in caplog.text
