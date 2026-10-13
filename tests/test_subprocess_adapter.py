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

"""Deterministic tests for the concrete Popen process adapter."""

from __future__ import annotations

import subprocess

import pytest

from castlearq_desktop.core.lifecycle_manager import LifecycleManager
from castlearq_desktop.core.lifecycle_manager import LifecycleState
from castlearq_desktop.core.lifecycle_manager import ProcessOwnershipUncertainError
from castlearq_desktop.core.lifecycle_manager import ProcessSpawnError
from castlearq_desktop.core.lifecycle_manager import build_launch_args
from castlearq_desktop.core.subprocess_adapter import SubprocessProcessAdapter

ABS_PYTHON = "/abs/env/bin/python"


class FakePopen:
    def __init__(self, exit_code=None, raises=None):
        self._exit_code = exit_code
        self._raises = raises or {}
        self.terminates = 0
        self.kills = 0
        self.poll_calls = 0

    def poll(self):
        self.poll_calls += 1
        action = self._raises.get("poll")
        if action is not None:
            raise action
        return self._exit_code

    def terminate(self):
        self.terminates += 1
        action = self._raises.get("terminate")
        if action is not None:
            raise action

    def kill(self):
        self.kills += 1
        action = self._raises.get("kill")
        if action is not None:
            raise action

    def exit(self, code=0):
        self._exit_code = code


def test_launch_args_built_from_validated_python():
    assert build_launch_args(ABS_PYTHON) == [
        ABS_PYTHON, "-m", "castlearq.main", "serve"]


def test_popen_kwargs_stdio_shell_cwd_env():
    popen = FakePopen()
    factory = PopenFactory(popen=popen)
    adapter = SubprocessProcessAdapter(popen_factory=factory)
    adapter.spawn(build_launch_args(ABS_PYTHON), shell=False)
    assert len(factory.calls) == 1
    call = factory.calls[0]
    assert call["args"][0] == [ABS_PYTHON, "-m", "castlearq.main", "serve"]
    assert call["kwargs"]["shell"] is False
    assert call["kwargs"]["stdin"] is subprocess.DEVNULL
    assert call["kwargs"]["stdout"] is subprocess.DEVNULL
    assert call["kwargs"]["stderr"] is subprocess.DEVNULL
    assert call["kwargs"].get("cwd") is None
    assert call["kwargs"].get("env") is None


def test_shell_true_refused_without_spawning():
    factory = PopenFactory()
    adapter = SubprocessProcessAdapter(popen_factory=factory)
    with pytest.raises(ProcessSpawnError):
        adapter.spawn(build_launch_args(ABS_PYTHON), shell=True)
    assert factory.calls == []


def test_successful_spawn_returns_owned_handle():
    popen = FakePopen()
    adapter = SubprocessProcessAdapter(popen_factory=PopenFactory(popen=popen))
    handle = adapter.spawn(build_launch_args(ABS_PYTHON), shell=False)
    assert handle.popen is popen
    assert handle.is_alive() is True
    assert handle.poll() is None


def test_status_and_exit_code():
    popen = FakePopen()
    adapter = SubprocessProcessAdapter(popen_factory=PopenFactory(popen=popen))
    handle = adapter.spawn(build_launch_args(ABS_PYTHON), shell=False)
    popen.exit(3)
    assert handle.is_alive() is False
    assert handle.poll() == 3


def test_creation_failure_is_definite_spawn_error():
    factory = PopenFactory(raises=FileNotFoundError("no such interpreter"))
    adapter = SubprocessProcessAdapter(popen_factory=factory)
    with pytest.raises(ProcessSpawnError):
        adapter.spawn(build_launch_args(ABS_PYTHON), shell=False)


def test_oserror_is_definite_spawn_error():
    factory = PopenFactory(raises=OSError("cannot fork"))
    adapter = SubprocessProcessAdapter(popen_factory=factory)
    with pytest.raises(ProcessSpawnError):
        adapter.spawn(build_launch_args(ABS_PYTHON), shell=False)


def test_generic_factory_failure_is_uncertain():
    factory = PopenFactory(raises=RuntimeError("boom"))
    adapter = SubprocessProcessAdapter(popen_factory=factory)
    with pytest.raises(ProcessOwnershipUncertainError):
        adapter.spawn(build_launch_args(ABS_PYTHON), shell=False)


def test_none_handle_is_uncertain():
    factory = PopenFactory(popen=None)
    factory._popen = None
    adapter = SubprocessProcessAdapter(popen_factory=factory)
    with pytest.raises(ProcessOwnershipUncertainError):
        adapter.spawn(build_launch_args(ABS_PYTHON), shell=False)


def test_observation_errors_propagate_for_manager_unknown():
    popen = FakePopen(raises={"poll": RuntimeError("poll down")})
    adapter = SubprocessProcessAdapter(popen_factory=PopenFactory(popen=popen))
    handle = adapter.spawn(build_launch_args(ABS_PYTHON), shell=False)
    with pytest.raises(RuntimeError):
        handle.poll()


class BrokenIsAlivePopen(FakePopen):
    def poll(self):
        raise RuntimeError("poll down")

    def _is_alive_probe(self):
        raise RuntimeError("status down")


def test_signals_target_only_owned_child():
    first = FakePopen()
    second = FakePopen()
    adapter = SubprocessProcessAdapter(
        popen_factory=PopenFactory(popen=first))
    handle = adapter.spawn(build_launch_args(ABS_PYTHON), shell=False)
    handle.terminate()
    handle.kill()
    assert first.terminates == 1
    assert first.kills == 1
    assert second.terminates == 0
    assert second.kills == 0


def test_no_blocking_waits_in_adapter():
    import inspect
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "src/castlearq_desktop/core/subprocess_adapter.py").read_text()
    for forbidden in ("time.sleep", ".wait(", ".communicate(", "QThread",
                      "concurrent.futures", "threading.Thread"):
        assert forbidden not in src
    assert "sleep" not in inspect.getsource(
        __import__("castlearq_desktop.core.subprocess_adapter",
                   fromlist=["SubprocessProcessHandle"]))


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


def test_compatible_with_lifecycle_manager_running():
    popen = FakePopen()
    adapter = SubprocessProcessAdapter(popen_factory=PopenFactory(popen=popen))
    mgr = LifecycleManager(adapter=adapter, clock=FakeClock())
    res = mgr.start(ABS_PYTHON)
    assert res.status == "ok"
    assert mgr.state is LifecycleState.RUNNING
    assert mgr.has_owned_handle is True


def test_compatible_with_lifecycle_manager_definite_failure():
    factory = PopenFactory(raises=FileNotFoundError("missing"))
    adapter = SubprocessProcessAdapter(popen_factory=factory)
    mgr = LifecycleManager(adapter=adapter, clock=FakeClock())
    res = mgr.start(ABS_PYTHON)
    assert res.status == "failed"
    assert mgr.state is LifecycleState.START_FAILED
    assert mgr.has_owned_handle is False
    assert mgr.ownership_unresolved is False


def test_compatible_with_lifecycle_manager_uncertain():
    factory = PopenFactory(raises=RuntimeError("indeterminate"))
    adapter = SubprocessProcessAdapter(popen_factory=factory)
    mgr = LifecycleManager(adapter=adapter, clock=FakeClock())
    res = mgr.start(ABS_PYTHON)
    assert res.status == "failed"
    assert mgr.ownership_unresolved is True


class PopenFactory:
    def __init__(self, popen=None, raises=None):
        self.calls = []
        self._popen = popen if popen is not None else FakePopen()
        self._raises = raises

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        if self._raises is not None:
            raise self._raises
        return self._popen
