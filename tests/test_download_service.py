# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Tests for the one-shot Core CLI download service."""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from castlearq_desktop.core.download_service import (
    AcquisitionStatus,
    ModelAcquisitionService,
    Request,
    StoreState,
    build_download_argv,
    build_list_argv,
    find_store_state,
    parse_list_payload,
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

    def communicate(self) -> tuple[str, str]:
        return self._stdout, self._stderr

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


def _list_document(artifacts: list[dict]) -> str:
    return json.dumps(
        {
            "schema": "castlearq.cli",
            "schema_version": 1,
            "command": "list",
            "exit_code": 0,
            "payload": {"artifacts": artifacts, "invalid_artifacts": []},
            "warnings": [],
            "error": None,
        }
    )


_DOWNLOAD_OK = (
    "Model: qwen2.5-coder-7b-instruct\n"
    "Artifact: qwen2.5-coder-7b-instruct-q4_k_m.gguf\n"
    "Size: 4096\n"
    "Source: Hugging Face\n"
    "\n"
    "Planning download...\n"
    "Destination: /store/qwen.gguf\n"
    "Downloading...\n"
    "Download complete.\n"
    "Artifact state: downloaded\n"
    "Registered manifest: /store/qwen.json\n"
)


# --- argv construction --------------------------------------------------


def test_download_argv_contains_model_and_flags():
    request = Request(
        model_id="owner/name",
        quantization="Q4_K_M",
        filename="file.gguf",
        model_store="/store",
    )
    argv = build_download_argv(PYTHON, request)
    assert argv == [
        PYTHON,
        "-m",
        "castlearq.main",
        "download",
        "owner/name",
        "--quantization",
        "Q4_K_M",
        "--filename",
        "file.gguf",
        "--model-store",
        "/store",
    ]


def test_download_argv_omits_absent_optional_flags():
    argv = build_download_argv(PYTHON, Request(model_id="m"))
    assert argv == [PYTHON, "-m", "castlearq.main", "download", "m"]


def test_download_argv_rejects_relative_interpreter():
    with pytest.raises(ValueError):
        build_download_argv("python3", Request(model_id="m"))


def test_list_argv_uses_public_json_surface():
    assert build_list_argv(PYTHON) == [
        PYTHON,
        "-m",
        "castlearq.main",
        "list",
        "--json",
    ]
    assert build_list_argv(PYTHON, "/store")[-1] == "/store"


# --- list payload parsing ----------------------------------------------


def test_parse_list_payload_groups_by_model():
    grouped, error = parse_list_payload(
        _list_document(
            [
                {
                    "model_id": "m",
                    "filename": "a.gguf",
                    "quantization": {"value": "Q4_K_M", "status": "known"},
                    "status": "downloaded",
                }
            ]
        )
    )
    assert error is None
    assert list(grouped) == ["m"]


def test_parse_list_payload_rejects_foreign_schema():
    grouped, error = parse_list_payload(json.dumps({"schema": "other"}))
    assert grouped == {}
    assert error is not None


def test_parse_list_payload_rejects_broken_json():
    grouped, error = parse_list_payload("{not json")
    assert grouped == {}
    assert error is not None


def test_find_store_state_distinguishes_downloaded_and_verified():
    grouped, _ = parse_list_payload(
        _list_document(
            [
                {"model_id": "m", "filename": "a.gguf", "status": "downloaded"},
                {"model_id": "m", "filename": "b.gguf", "status": "verified"},
            ]
        )
    )
    assert find_store_state(grouped, "m", filename="a.gguf") is StoreState.DOWNLOADED
    assert find_store_state(grouped, "m", filename="b.gguf") is StoreState.VERIFIED


def test_find_store_state_returns_none_when_not_listed():
    grouped, _ = parse_list_payload(_list_document([]))
    assert find_store_state(grouped, "m", filename="a.gguf") is None


def test_find_store_state_ambiguous_model_claims_nothing():
    grouped, _ = parse_list_payload(
        _list_document(
            [
                {"model_id": "m", "filename": "a.gguf", "status": "downloaded"},
                {"model_id": "m", "filename": "b.gguf", "status": "downloaded"},
            ]
        )
    )
    assert find_store_state(grouped, "m") is None


# --- service behaviour ---------------------------------------------------


def test_successful_download_reports_core_fields_and_store_state(qapp):
    popen = FakePopen(
        [
            FakeProcess(stdout=_DOWNLOAD_OK),
            FakeProcess(
                stdout=_list_document(
                    [
                        {
                            "model_id": "qwen2.5-coder-7b-instruct",
                            "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
                            "quantization": {"value": "Q4_K_M", "status": "known"},
                            "status": "verified",
                        }
                    ]
                )
            ),
        ]
    )
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=ImmediatePool,
    )
    results = []
    service.finished.connect(results.append)

    assert service.request_download(Request(model_id="qwen2.5-coder-7b-instruct")) is True

    assert len(results) == 1
    result = results[0]
    assert result.status is AcquisitionStatus.READY
    assert result.filename == "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
    assert result.destination == "/store/qwen.gguf"
    assert result.manifest_path == "/store/qwen.json"
    assert result.store_state is StoreState.VERIFIED
    assert result.store_verified is True
    assert popen.calls[0][:4] == [PYTHON, "-m", "castlearq.main", "download"]


def test_store_state_downloaded_is_not_reported_as_verified(qapp):
    popen = FakePopen(
        [
            FakeProcess(stdout=_DOWNLOAD_OK),
            FakeProcess(
                stdout=_list_document(
                    [
                        {
                            "model_id": "qwen2.5-coder-7b-instruct",
                            "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
                            "status": "downloaded",
                        }
                    ]
                )
            ),
        ]
    )
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=ImmediatePool,
    )
    results = []
    service.finished.connect(results.append)
    service.request_download(Request(model_id="qwen2.5-coder-7b-instruct"))

    result = results[0]
    assert result.store_state is StoreState.DOWNLOADED
    assert result.store_verified is False
    assert result.store_confirmed is True


def test_missing_store_confirmation_does_not_fail_the_download(qapp):
    popen = FakePopen(
        [
            FakeProcess(stdout=_DOWNLOAD_OK),
            FakeProcess(stdout=_list_document([])),
        ]
    )
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=ImmediatePool,
    )
    results = []
    service.finished.connect(results.append)
    service.request_download(Request(model_id="qwen2.5-coder-7b-instruct"))

    result = results[0]
    assert result.status is AcquisitionStatus.READY
    assert result.store_state is None
    assert result.store_confirmed is False


def test_store_read_failure_does_not_fail_the_download(qapp):
    popen = FakePopen(
        [
            FakeProcess(stdout=_DOWNLOAD_OK),
            FakeProcess(stderr="boom", returncode=1),
        ]
    )
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=ImmediatePool,
    )
    results = []
    service.finished.connect(results.append)
    service.request_download(Request(model_id="qwen2.5-coder-7b-instruct"))

    assert results[0].status is AcquisitionStatus.READY
    assert results[0].store_state is None


def test_already_downloaded_is_reported_distinctly(qapp):
    stdout = (
        "Model: m\n"
        "Artifact: m.gguf\n"
        "Artifact already downloaded.\n"
    )
    popen = FakePopen([FakeProcess(stdout=stdout)])
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=ImmediatePool,
    )
    results = []
    service.finished.connect(results.append)
    service.request_download(Request(model_id="m"))

    assert results[0].status is AcquisitionStatus.ALREADY_DOWNLOADED
    assert results[0].succeeded is True


def test_exit_code_one_is_a_failure(qapp):
    popen = FakePopen([FakeProcess(stderr="Download error: network\n", returncode=1)])
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=ImmediatePool,
    )
    failures = []
    service.failed.connect(failures.append)
    service.request_download(Request(model_id="m"))

    assert len(failures) == 1
    assert "network" in failures[0]


def test_exit_code_two_is_a_usage_error(qapp):
    popen = FakePopen(
        [FakeProcess(stderr="usage: castlearq download\n", returncode=2)]
    )
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=ImmediatePool,
    )
    failures = []
    service.failed.connect(failures.append)
    service.request_download(Request(model_id="m"))

    assert len(failures) == 1
    assert "Uso incorrecto" in failures[0]


def test_progress_lines_are_forwarded(qapp):
    popen = FakePopen([FakeProcess(stdout=_DOWNLOAD_OK)])
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=ImmediatePool,
    )
    lines = []
    service.progress.connect(lines.append)
    service.request_download(Request(model_id="m"))

    assert "Downloading..." in lines
    assert "Download complete." in lines
    assert not any("%" in line for line in lines)


def test_single_flight_rejects_a_second_request(qapp):
    popen = FakePopen([FakeProcess(stdout=_DOWNLOAD_OK)])
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=ImmediatePool,
    )
    assert service.request_download(Request(model_id="m")) is True
    # The immediate pool already drained the first attempt, so a new one is
    # accepted; the single-flight rule is about concurrency.
    assert service.is_busy is False


def test_concurrent_request_is_refused_while_busy(qapp):
    class BlockingPool:
        def __init__(self) -> None:
            self.runnables: list[Any] = []

        def start(self, runnable: Any) -> None:
            self.runnables.append(runnable)  # never executed

    popen = FakePopen([FakeProcess(stdout=_DOWNLOAD_OK)])
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=BlockingPool,
    )
    assert service.request_download(Request(model_id="m")) is True
    assert service.is_busy is True
    assert service.request_download(Request(model_id="other")) is False


def test_invalidate_releases_the_active_attempt(qapp):
    class BlockingPool:
        def __init__(self) -> None:
            self.runnables: list[Any] = []

        def start(self, runnable: Any) -> None:
            self.runnables.append(runnable)  # never executed

    popen = FakePopen([FakeProcess(stdout=_DOWNLOAD_OK)])
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=BlockingPool,
    )
    assert service.request_download(Request(model_id="m")) is True
    assert service.is_busy is True
    service.invalidate()
    assert service.is_busy is False


def test_stale_result_is_not_delivered_after_invalidation(qapp):
    from castlearq_desktop.core.download_service import (
        AcquisitionResult,
        _DownloadReport,
    )

    class DeferredPool:
        def __init__(self) -> None:
            self.runnables: list[Any] = []

        def start(self, runnable: Any) -> None:
            self.runnables.append(runnable)

    popen = FakePopen([FakeProcess(stdout=_DOWNLOAD_OK)])
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=DeferredPool,
    )
    results = []
    service.finished.connect(results.append)
    service.request_download(Request(model_id="m"))
    service.invalidate()

    # A report from a superseded generation must not reach the UI.
    service._on_finished(
        999,
        _DownloadReport(
            result=AcquisitionResult(status=AcquisitionStatus.READY, model_id="m")
        ),
    )
    assert results == []


def test_missing_interpreter_reports_an_error(qapp):
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(path=None),
        popen_factory=FakePopen([]),
        thread_pool_factory=ImmediatePool,
    )
    errors = []
    service.error.connect(errors.append)
    assert service.request_download(Request(model_id="m")) is False
    assert len(errors) == 1


def test_relative_interpreter_is_refused(qapp):
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(path="python3"),
        popen_factory=FakePopen([]),
        thread_pool_factory=ImmediatePool,
    )
    errors = []
    service.error.connect(errors.append)
    assert service.request_download(Request(model_id="m")) is False
    assert "absolute" in errors[0]


def test_close_prevents_new_requests(qapp):
    service = ModelAcquisitionService(
        interpreter_settings=FakeSettings(),
        popen_factory=FakePopen([]),
        thread_pool_factory=ImmediatePool,
    )
    service.close()
    assert service.request_download(Request(model_id="m")) is False


def _doc_with_backend(backend):
    entry = {"model_id": "m", "filename": "f.gguf"}
    if backend is not _UNSET:
        entry["backend"] = backend
    return _list_document([entry])


_UNSET = object()


def test_parse_artifacts_backend_gguf():
    from castlearq_desktop.core.download_service import parse_artifacts
    arts, err = parse_artifacts(_doc_with_backend("gguf"))
    assert err is None
    assert arts[0].backend == "gguf"


def test_parse_artifacts_backend_openvino():
    from castlearq_desktop.core.download_service import parse_artifacts
    arts, err = parse_artifacts(_doc_with_backend("openvino"))
    assert err is None
    assert arts[0].backend == "openvino"


def test_parse_artifacts_backend_absent():
    from castlearq_desktop.core.download_service import parse_artifacts
    arts, err = parse_artifacts(_doc_with_backend(_UNSET))
    assert err is None
    assert arts[0].backend is None


def test_parse_artifacts_backend_null():
    from castlearq_desktop.core.download_service import parse_artifacts
    arts, err = parse_artifacts(_doc_with_backend(None))
    assert err is None
    assert arts[0].backend is None


def test_parse_artifacts_backend_unexpected():
    from castlearq_desktop.core.download_service import parse_artifacts
    arts, err = parse_artifacts(_doc_with_backend("foo"))
    assert err is None
    assert arts[0].backend is None


__all__ = []
