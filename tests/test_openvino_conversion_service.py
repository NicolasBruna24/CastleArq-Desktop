# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

"""Tests for the asynchronous OpenVINO conversion service.

These tests drive the public CLI surface of CastleArq Core through injected
``popen_factory`` doubles. No conversion is executed, no model is downloaded
and no ``optimum-cli`` is required: the interpreter path, the conversion CLI
and the local-library query are all scripted.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable, Optional
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QThreadPool

from castlearq_desktop.core.download_service import LocalArtifact, StoreState
from castlearq_desktop.core.openvino_conversion_service import (
    CONVERSION_SUCCESS_MESSAGE,
    CONVERSION_TIMEOUT_SECONDS,
    ERROR_ARTIFACT_NOT_REGISTERED,
    ERROR_CONVERSION_FAILED,
    ERROR_CONVERSION_TIMEOUT,
    ERROR_INVALID_INPUT,
    ERROR_INVALID_JSON,
    ERROR_NO_INTERPRETER_CONFIGURED,
    ERROR_PROCESS_START_FAILED,
    ConversionRequest,
    ConversionResult,
    ConversionValidationError,
    OpenVINOConversionService,
    _build_conversion_argv,
)

PYTHON = "/usr/bin/python3"


class FakeSettings:
    """Minimal interpreter-settings double."""

    def __init__(self, path: Optional[str] = PYTHON) -> None:
        self._path = path

    def load(self) -> Optional[str]:
        return self._path


class FakeProcess:
    """Minimal ``subprocess.Popen`` double."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.argv: list[str] = []

    def communicate(self, timeout: Optional[float] = None) -> tuple[str, str]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    def poll(self) -> int:
        return self.returncode


class FakePopen:
    """Popen factory that replays a scripted sequence of processes."""

    def __init__(self, scripts: list[FakeProcess]) -> None:
        self._scripts = list(scripts)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append(list(argv))
        if not self._scripts:
            raise AssertionError(f"unexpected extra call: {argv}")
        return self._scripts.pop(0)


class ImmediatePool:
    """Thread-pool double that runs the runnable synchronously."""

    def __init__(self) -> None:
        self.runnables: list[Any] = []

    def start(self, runnable: Any) -> None:
        self.runnables.append(runnable)
        runnable.run()

    def waitForDone(self, timeout: float) -> None:
        pass


def _make_service(
    *,
    settings: Optional[FakeSettings] = None,
    popen_factory: Optional[Callable[..., Any]] = None,
    pool_factory: Optional[Callable[[], ImmediatePool]] = None,
) -> OpenVINOConversionService:
    if settings is None:
        settings = FakeSettings()
    return OpenVINOConversionService(
        settings,
        popen_factory=popen_factory,
        thread_pool_factory=lambda: (pool_factory or ImmediatePool)(),
    )


def _conversion_envelope(payload: dict, exit_code: int = 0) -> str:
    return json.dumps(
        {
            "schema": "castlearq.cli",
            "schema_version": 1,
            "command": "convert",
            "exit_code": exit_code,
            "payload": payload,
            "warnings": [],
            "error": None,
        }
    )


def _list_envelope(artifacts: list[dict], exit_code: int = 0) -> str:
    return json.dumps(
        {
            "schema": "castlearq.cli",
            "schema_version": 1,
            "command": "list",
            "exit_code": exit_code,
            "payload": {"artifacts": artifacts, "invalid_artifacts": []},
            "warnings": [],
            "error": None,
        }
    )


def _openvino_entry(
    model_id: str = "m",
    filename: str = "m.gguf",
    quantization: Optional[str] = "fp16",
    status: str = "verified",
    backend: str = "openvino",
) -> dict:
    return {
        "filename": filename,
        "model_id": model_id,
        "problem": None,
        "quantization": (
            {"status": "known", "value": quantization} if quantization is not None else None
        ),
        "status": status,
        "backend": backend,
    }


# --- argv construction --------------------------------------------


def test_build_argv_huggingface():
    request = ConversionRequest(
        modality="huggingface", artifact_id="owner/model", precision="fp16"
    )
    argv = _build_conversion_argv(PYTHON, request)
    assert argv == [
        PYTHON,
        "-m",
        "castlearq.main",
        "convert",
        "owner/model",
        "--quantization",
        "fp16",
        "--json",
    ]


def test_build_argv_local():
    request = ConversionRequest(
        modality="local",
        artifact_id="model-locale",
        source_path="/store/models/m",
        precision="int8",
    )
    argv = _build_conversion_argv(PYTHON, request)
    assert argv == [
        PYTHON,
        "-m",
        "castlearq.main",
        "convert",
        "model-locale",
        "--source-path",
        "/store/models/m",
        "--quantization",
        "int8",
        "--json",
    ]


def test_build_argv_local_path_with_spaces():
    request = ConversionRequest(
        modality="local",
        artifact_id="m",
        source_path="/tmp/mi modelo/conversos",
        precision="fp16",
    )
    argv = _build_conversion_argv(PYTHON, request)
    assert "--source-path" in argv
    assert argv[argv.index("--source-path") + 1] == "/tmp/mi modelo/conversos"
    # The path must remain a single element, never shell-joined.
    assert argv.count("/tmp/mi modelo/conversos") == 1


def test_build_argv_rejects_relative_interpreter():
    with pytest.raises(ConversionValidationError):
        _build_conversion_argv("python3", ConversionRequest("huggingface", "m"))


def test_build_argv_rejects_invalid_modality():
    with pytest.raises(ConversionValidationError):
        _build_conversion_argv(PYTHON, ConversionRequest(modality="gpu", artifact_id="m"))


def test_build_argv_rejects_unknown_precision():
    with pytest.raises(ConversionValidationError):
        _build_conversion_argv(PYTHON, ConversionRequest("local", "m", precision="fp32"))


def test_build_argv_rejects_missing_source_path_local():
    with pytest.raises(ConversionValidationError):
        _build_conversion_argv(PYTHON, ConversionRequest(modality="local", artifact_id="m"))

# --- interpreter configuration ------------------------------------


def test_uses_configured_interpreter_and_builds_argv(qapp):
    popen = FakePopen([])
    service = _make_service(popen_factory=popen, pool_factory=ImmediatePool)
    request = ConversionRequest(modality="huggingface", artifact_id="m", precision="fp16")
    assert service.convert(request) is True
    assert popen.calls, "the conversion CLI must be invoked"
    argv = popen.calls[0]
    assert argv[0] == PYTHON
    assert argv[1:4] == ["-m", "castlearq.main", "convert"]
    assert argv[4] == "m"
    assert argv[5:8] == ["--quantization", "fp16", "--json"]


def test_relative_interpreter_rejected_without_starting_process(qapp):
    service = _make_service(settings=FakeSettings("python3"))
    request = ConversionRequest(modality="huggingface", artifact_id="m")
    assert service.convert(request) is False
    assert service._argv is None
    assert service._argv is None


def test_missing_interpreter_rejected(qapp):
    service = _make_service(settings=FakeSettings(None))
    request = ConversionRequest(modality="huggingface", artifact_id="m")
    assert service.convert(request) is False
    assert service._argv is None


def test_invalid_precision_rejected_without_starting_process(qapp):
    service = _make_service()
    request = ConversionRequest(modality="huggingface", artifact_id="m", precision="fp32")
    results = []
    service.completed.connect(results.append)
    assert service.convert(request) is False
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error_code == ERROR_INVALID_INPUT
    assert service._argv is None

    quantization: Optional[str] = "fp16",
    status: str = "verified",
# --- success and artefact confirmation -----------------------------


def _run_service(
    popen: FakePopen, request: ConversionRequest, pool_factory: Any = ImmediatePool
) -> tuple[bool, list[ConversionResult]]:
    service = _make_service(popen_factory=popen, pool_factory=pool_factory)
    results: list[ConversionResult] = []
    service.completed.connect(results.append)
    started = service.convert(request)
    return started, results


def test_success_confirms_openvino_artefact_registered(qapp):
    conv_payload = {
        "model_id": "m",
        "artifact_filename": "m-converted.gguf",
        "quantization": "fp16",
        "backend": "openvino",
    }
    library_entry = _openvino_entry(
        model_id="m",
        filename="m-converted.gguf",
        quantization="fp16",
        status="verified",
        backend="openvino",
    )
    popen = FakePopen(
        [
            FakeProcess(stdout=_conversion_envelope(conv_payload), returncode=0),
            FakeProcess(stdout=_list_envelope([library_entry]), returncode=0),
        ]
    )
    started, results = _run_service(popen, ConversionRequest("huggingface", "m"))
    assert started is True
    assert len(results) == 1
    result = results[0]
    assert result.success is True
    assert result.message == CONVERSION_SUCCESS_MESSAGE
    assert result.artifact is not None
    assert result.artifact.model_id == "m"
    assert result.artifact.filename == "m-converted.gguf"
    assert result.artifact.quantization == "fp16"
    assert result.artifact.backend == "openvino"
    assert result.artifact.state == StoreState.VERIFIED


def test_artefact_absent_after_declared_success(qapp):
    conv_payload = {"model_id": "m", "artifact_filename": "m.gguf", "quantization": "fp16"}
    popen = FakePopen(
        [
            FakeProcess(stdout=_conversion_envelope(conv_payload), returncode=0),
            FakeProcess(stdout=_list_envelope([]), returncode=0),
        ]
    )
    started, results = _run_service(popen, ConversionRequest("huggingface", "m"))
    assert started is True
    assert len(results) == 1
    result = results[0]
    assert result.success is False
    assert result.error_code == ERROR_ARTIFACT_NOT_REGISTERED
    assert result.artifact is None


def test_artefact_present_but_not_openvino_backend(qapp):
    conv_payload = {"model_id": "m", "artifact_filename": "m.gguf", "quantization": "fp16"}
    library_entry = _openvino_entry(model_id="m", filename="m.gguf", backend="gguf")
    popen = FakePopen(
        [
            FakeProcess(stdout=_conversion_envelope(conv_payload), returncode=0),
            FakeProcess(stdout=_list_envelope([library_entry]), returncode=0),
        ]
    )
    started, results = _run_service(popen, ConversionRequest("huggingface", "m"))
    assert started is True
    assert len(results) == 1
    result = results[0]
    assert result.success is False
    assert result.error_code == ERROR_ARTIFACT_NOT_REGISTERED
    assert result.artifact is None


def test_ambiguous_same_identity_does_not_pick_arbitrarily(qapp):
    conv_payload = {"model_id": "m", "artifact_filename": "m.gguf", "quantization": "fp16"}
    entries = [
        _openvino_entry(model_id="m", filename="m.gguf", quantization="fp16", status="verified"),
        _openvino_entry(model_id="m", filename="m.gguf", quantization="int8", status="verified"),
    ]
    popen = FakePopen(
        [
            FakeProcess(stdout=_conversion_envelope(conv_payload), returncode=0),
            FakeProcess(stdout=_list_envelope(entries), returncode=0),
        ]
    )
    started, results = _run_service(popen, ConversionRequest("huggingface", "m"))
    assert started is True
    assert len(results) == 1
    result = results[0]
    assert result.success is False
    assert result.error_code == ERROR_ARTIFACT_NOT_REGISTERED


# --- error paths -------------------------------------------------------


def test_conversion_non_zero_exit_reported(qapp):
    popen = FakePopen(
        [
            FakeProcess(stdout="some error", stderr="boom", returncode=1),
            FakeProcess(stdout="", returncode=0),
        ]
    )
    started, results = _run_service(popen, ConversionRequest("huggingface", "m"))
    assert started is True
    assert len(results) == 1
    result = results[0]
    assert result.success is False
    assert result.error_code == ERROR_CONVERSION_FAILED
    assert "boom" in result.message


# --- timeout ---------------------------------------------------------

_TEST_TIMEOUT = 1  # seconds


def test_conversion_timeout(qapp):
    import castlearq_desktop.core.openvino_conversion_service as ocs

    popen_factory = lambda argv, **kwargs: subprocess.Popen(
        ["sleep", "30"],
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    service = _make_service(popen_factory=popen_factory, pool_factory=ImmediatePool)
    results: list[ConversionResult] = []
    service.completed.connect(results.append)
    with patch.object(ocs, "CONVERSION_TIMEOUT_SECONDS", _TEST_TIMEOUT):
        service.convert(ConversionRequest("huggingface", "m"))
    assert len(results) == 1
    result = results[0]
    assert result.success is False
    assert result.error_code == ERROR_CONVERSION_TIMEOUT


def test_invalid_json_reported(qapp):
    popen = FakePopen(
        [
            FakeProcess(stdout="not json at all", returncode=0),
            FakeProcess(stdout="", returncode=0),
        ]
    )
    started, results = _run_service(popen, ConversionRequest("huggingface", "m"))
    assert started is True
    assert len(results) == 1
    result = results[0]
    assert result.success is False
    assert result.error_code == ERROR_INVALID_JSON


def test_process_start_failed(qapp):
    def failing_popen(argv, **kwargs):
        raise FileNotFoundError("/no/such/python")

    started, results = _run_service(failing_popen, ConversionRequest("huggingface", "m"))
    assert started is True
    assert len(results) == 1
    result = results[0]
    assert result.success is False
    assert result.error_code == ERROR_PROCESS_START_FAILED


# --- single-flight & Qt signal behaviour -------------------------------


class DeferredPool:
    """Thread-pool double that records runnables without running them.

    Lets a test keep a conversion "in flight" (``is_busy`` True) and decide
    exactly when the worker executes, so single-flight rejection and busy-state
    transitions can be observed deterministically rather than racing a real
    thread.
    """

    def __init__(self) -> None:
        self.runnables: list[Any] = []

    def start(self, runnable: Any) -> None:
        self.runnables.append(runnable)

    def waitForDone(self, timeout: float) -> None:
        pass


def _one_success_sequence() -> FakePopen:
    conv_payload = {
        "model_id": "m",
        "artifact_filename": "m-converted.gguf",
        "quantization": "fp16",
        "backend": "openvino",
    }
    entry = _openvino_entry(
        model_id="m",
        filename="m-converted.gguf",
        quantization="fp16",
        status="verified",
        backend="openvino",
    )
    return FakePopen(
        [
            FakeProcess(stdout=_conversion_envelope(conv_payload), returncode=0),
            FakeProcess(stdout=_list_envelope([entry]), returncode=0),
        ]
    )


def test_second_conversion_while_in_flight_is_rejected(qapp):
    deferred = DeferredPool()
    service = _make_service(
        popen_factory=_one_success_sequence(),
        pool_factory=lambda: deferred,
    )
    results: list[ConversionResult] = []
    service.completed.connect(results.append)

    request = ConversionRequest("huggingface", "m")
    # The first conversion is queued but not yet executed (deferred pool),
    # so the service must still be marked busy.
    assert service.convert(request) is True
    assert service.is_busy is True

    # A second concurrent conversion must be rejected and reported through
    # `completed`, without starting a second worker.
    assert service.convert(request) is False
    assert service.is_busy is True
    assert len(deferred.runnables) == 1  # no second worker queued
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error_code == ERROR_INVALID_INPUT
    assert "en curso" in results[0].message

    # The in-flight worker, when finally run, still delivers its own result and
    # clears the busy state exactly once.
    deferred.runnables[0].run()
    assert service.is_busy is False


def test_busy_cleared_on_success(qapp):
    """A successful conversion clears the busy flag and emits one result.

    Uses ``ImmediatePool`` so the worker's ``run()`` executes inline during the
    ``convert()`` call; by the time ``convert()`` returns the conversion has
    already completed, and ``busy_changed`` recorded the True -> False transition.
    """
    service = _make_service(
        popen_factory=_one_success_sequence(),
        pool_factory=ImmediatePool,
    )
    busy: list[bool] = []
    results: list[ConversionResult] = []
    service.busy_changed.connect(busy.append)
    service.completed.connect(results.append)

    assert service.convert(ConversionRequest("huggingface", "m")) is True

    # ImmediatePool ran the worker inline, so completion already happened.
    assert service.is_busy is False
    assert busy == [True, False]
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].error_code is None


def test_busy_cleared_on_failure(qapp):
    """A failing conversion still clears busy and emits one failure result."""

    def failing_popen(argv, **kwargs):  # noqa: ANN001, ANN002
        raise FileNotFoundError("/no/such/core")

    service = _make_service(
        popen_factory=failing_popen,
        pool_factory=ImmediatePool,
    )
    busy: list[bool] = []
    results: list[ConversionResult] = []
    service.busy_changed.connect(busy.append)
    service.completed.connect(results.append)

    assert service.convert(ConversionRequest("huggingface", "m")) is True

    # The worker failed inline; busy must still clear exactly once.
    assert service.is_busy is False
    assert busy == [True, False]
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error_code == ERROR_PROCESS_START_FAILED



def test_completed_signal_delivered_across_thread_pool(qapp, qtbot):
    """A real QThreadPool delivers the result through Qt's queued connection.

    Exercises the same signal bridge a production QThreadPool uses: the worker
    emits on its own thread, and _on_finished (hence completed) is invoked on the
    GUI thread via the event loop.
    """
    service = _make_service(
        popen_factory=_one_success_sequence(),
        pool_factory=lambda: QThreadPool.globalInstance(),
    )
    results: list[ConversionResult] = []
    service.completed.connect(results.append)

    with qtbot.waitSignal(service.completed, timeout=5000):
        assert service.convert(ConversionRequest("huggingface", "m")) is True

    QThreadPool.globalInstance().waitForDone(5000)
    assert len(results) == 1
    result = results[0]
    assert result.success is True
    assert result.artifact is not None
    assert result.artifact.model_id == "m"
    assert result.artifact.backend == "openvino"
    assert service.is_busy is False


def test_invalidate_drops_late_result(qapp):
    """A result arriving after invalidate() is dropped and never reaches the UI."""
    deferred = DeferredPool()
    service = _make_service(
        popen_factory=_one_success_sequence(),
        pool_factory=lambda: deferred,
    )
    results: list[ConversionResult] = []
    service.completed.connect(results.append)

    assert service.convert(ConversionRequest("huggingface", "m")) is True
    assert service.is_busy is True

    # Window closes / Core shuts down before the worker finishes. invalidate()
    # retires the active generation, so the service is no longer "busy".
    service.invalidate()
    # invalidate() retires the active generation (result dropped) but the
    # worker may still be running; the service stays busy to prevent a second
    # concurrent conversion. busy clears only when the worker actually finishes.
    assert service.is_busy is True

    # The worker eventually reports, but its result is superseded and dropped.
    deferred.runnables[0].run()
    assert results == []
    assert service.is_busy is False
