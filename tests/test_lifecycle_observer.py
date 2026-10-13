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

"""Deterministic tests for the QTimer lifecycle observation controller."""

from __future__ import annotations

import pytest

from castlearq_desktop.core.lifecycle_manager import LifecycleManager
from castlearq_desktop.core.lifecycle_manager import LifecycleState
from castlearq_desktop.core.lifecycle_observer import OBSERVE_INTERVAL_MS
from castlearq_desktop.core.lifecycle_observer import LifecycleObserver

ABS_PYTHON = "/abs/env/bin/python"


class FakeSignal:
    def __init__(self) -> None:
        self.connections = []

    def connect(self, slot):
        self.connections.append(slot)


class FakeTimer:
    instances = []

    def __init__(self) -> None:
        self.timeout = FakeSignal()
        self._interval = None
        self._active = False
        self.start_calls = 0
        FakeTimer.instances.append(self)

    def setInterval(self, ms):
        self._interval = ms

    def interval(self):
        return self._interval

    def isActive(self):
        return self._active

    def start(self):
        self.start_calls += 1
        self._active = True

    def stop(self):
        self._active = False

    def fire(self):
        for slot in list(self.timeout.connections):
            slot()


class FakeManager:
    def __init__(self, behavior="ok") -> None:
        self.calls = 0
        self.behavior = behavior

    def observe(self):
        self.calls += 1
        if self.behavior == "boom":
            raise RuntimeError("probe failed")


class FakeHandle:
    def __init__(self, *, alive=True, exit_code=None):
        self._alive = alive
        self._exit_code = exit_code

    def is_alive(self):
        return self._alive

    def poll(self):
        return None if self._alive else self._exit_code

    def terminate(self):
        pass

    def kill(self):
        pass

    def exit(self, code=0):
        self._alive = False
        self._exit_code = code
class UnknownHandle(FakeHandle):
    def poll(self):
        raise RuntimeError("poll unavailable")

    def is_alive(self):
        raise RuntimeError("status unavailable")


class FakeAdapter:
    def __init__(self, handle):
        self.handle = handle

    def spawn(self, argv, *, shell):
        assert shell is False
        return self.handle


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


def make_manager(handle):
    return LifecycleManager(adapter=FakeAdapter(handle), clock=FakeClock())


@pytest.fixture(autouse=True)
def _reset_fake_timers():
    FakeTimer.instances.clear()
    yield
    FakeTimer.instances.clear()


def make_observer(manager, **kwargs):
    kwargs.setdefault("timer_factory", FakeTimer)
    return LifecycleObserver(manager, **kwargs)


def test_interval_is_nominal_100ms():
    assert OBSERVE_INTERVAL_MS == 100
    obs = make_observer(FakeManager())
    assert obs.interval_ms == 100
    assert obs.timer.interval() == 100


def test_start_and_stop_timer():
    obs = make_observer(FakeManager())
    assert obs.is_running() is False
    obs.start()
    assert obs.is_running() is True
    obs.stop()
    assert obs.is_running() is False


def test_start_idempotent_no_duplicates():
    obs = make_observer(FakeManager())
    obs.start()
    obs.start()
    assert len(FakeTimer.instances) == 1
    assert len(obs.timer.timeout.connections) == 1
    assert obs.timer.start_calls == 1


def test_observation_delegated_to_manager():
    manager = FakeManager()
    obs = make_observer(manager)
    obs.start()
    obs.timer.fire()
    obs.timer.fire()
    assert manager.calls == 2


def test_unknown_preserves_starting():
    mgr = make_manager(UnknownHandle())
    mgr.start(ABS_PYTHON)
    assert mgr.state is LifecycleState.STARTING
    obs = make_observer(mgr)
    obs.start()
    obs.timer.fire()
    assert mgr.state is LifecycleState.STARTING
    assert mgr.has_owned_handle is True


def test_running_only_after_confirmed_liveness():
    mgr = make_manager(UnknownHandle())
    mgr.start(ABS_PYTHON)
    assert mgr.state is LifecycleState.STARTING
    obs = make_observer(mgr)
    obs.start()
    mgr._handle = FakeHandle(alive=True)
    obs.timer.fire()
    assert mgr.state is LifecycleState.RUNNING


def test_confirmed_exit_handled_safely():
    mgr = make_manager(UnknownHandle())
    mgr.start(ABS_PYTHON)
    obs = make_observer(mgr)
    obs.start()
    exited = FakeHandle(alive=True)
    exited.exit(7)
    mgr._handle = exited
    obs.timer.fire()
    assert mgr.state is LifecycleState.UNEXPECTED_EXIT
    assert mgr.has_owned_handle is False
    assert obs.is_running() is True


def test_observer_exception_safe():
    manager = FakeManager(behavior="boom")
    obs = make_observer(manager)
    obs.start()
    obs.timer.fire()
    obs.timer.fire()
    assert manager.calls == 2
    assert obs.is_running() is True


def test_no_blocking_waits():
    import inspect
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "src/castlearq_desktop/core/lifecycle_observer.py").read_text()
    for forbidden in ("time.sleep", "QThread", "concurrent.futures",
                      "threading.Thread", "QTimer.singleShot"):
        assert forbidden not in src
    assert "sleep" not in inspect.getsource(LifecycleObserver._on_timeout)


def test_real_qtimer_interval_and_start_stop():
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    manager = FakeManager()
    obs = LifecycleObserver(manager)
    assert obs.timer.interval() == 100
    obs.start()
    try:
        assert obs.timer.isActive() is True
    finally:
        obs.stop()
    assert obs.timer.isActive() is False
