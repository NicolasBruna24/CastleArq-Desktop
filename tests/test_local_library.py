# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Tests for the local library service and view.

The library is read through Core's public CLI (``castlearq list --json``).
The fixtures below replay the *real* envelope shape observed from the Core
CLI, so the parsing is checked against the contract and not against an
invented one.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from PySide6.QtCore import Qt

from castlearq_desktop.core.download_service import (
    AcquisitionResult,
    AcquisitionStatus,
    LocalArtifact,
    LocalLibraryService,
    StoreState,
    parse_artifacts,
    parse_store_payload,
    read_quantization,
    read_store_state,
)
from castlearq_desktop.ui.local_library_view import (
    LocalLibraryView,
    artifact_lines,
    store_state_text,
)

PYTHON = "/usr/bin/python3"


def _envelope(artifacts: list[dict]) -> str:
    """Build a real ``castlearq.cli`` envelope for ``list``."""
    return json.dumps(
        {
            "command": "list",
            "error": None,
            "exit_code": 0,
            "payload": {"artifacts": artifacts, "invalid_artifacts": []},
            "schema": "castlearq.cli",
            "schema_version": 1,
            "warnings": [],
        }
    )


def _artifact(
    model_id: str = "demo-model",
    filename: str = "demo.gguf",
    quantization: Optional[str] = "Q4_K_M",
    status: str = "verified",
    size_bytes: Optional[int] = 16,
    problem: Optional[str] = None,
) -> dict:
    """One artifact entry exactly as Core's ``list --json`` reports it."""
    return {
        "filename": filename,
        "model_id": model_id,
        "problem": problem,
        "quantization": (
            {"status": "known", "value": quantization}
            if quantization is not None
            else {"status": "unknown", "reason": "not_observed", "value": None}
        ),
        "size_bytes": size_bytes,
        "status": status,
    }


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


def _store_envelope(
    path: str = "/home/user/.local/share/castlearq/models",
    source: str = "default",
    exists: bool = True,
) -> str:
    """Build a real ``castlearq.cli`` envelope for ``store --json``."""
    return json.dumps(
        {
            "command": "store",
            "error": None,
            "exit_code": 0,
            "payload": {
                "exists": exists,
                "legacy_detected": False,
                "legacy_used": False,
                "path": path,
                "source": source,
            },
            "schema": "castlearq.cli",
            "schema_version": 1,
            "warnings": [],
        }
    )


class FakePopen:
    """Popen factory that replays a scripted sequence of processes.

    The library asks Core two things: ``list --json`` (the artifacts) and
    ``store --json`` (the store Core selected). Only the ``list`` answers are
    scripted; the ``store`` answer is a fixed envelope, because the store
    path is informative and never decides the outcome of the query.
    """

    def __init__(self, scripts: list[FakeProcess]) -> None:
        self._scripts = list(scripts)
        self.calls: list[list[str]] = []
        self.store_calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append(list(argv))
        if "store" in argv:
            self.store_calls.append(list(argv))
            return FakeProcess(stdout=_store_envelope())
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


def _service(
    scripts: list[FakeProcess], settings: Optional[FakeSettings] = None
) -> tuple[LocalLibraryService, FakePopen]:
    popen = FakePopen(scripts)
    service = LocalLibraryService(
        settings or FakeSettings(),
        popen_factory=popen,
        thread_pool_factory=ImmediatePool,
    )
    return service, popen


# --- parsing of the real listing -----------------------------------------


def test_parse_artifacts_reads_real_envelope():
    document = _envelope(
        [_artifact(), _artifact(filename="b.gguf", status="downloaded")]
    )
    artifacts, error = parse_artifacts(document)
    assert error is None
    assert [a.filename for a in artifacts] == ["b.gguf", "demo.gguf"]
    assert artifacts[1].model_id == "demo-model"
    assert artifacts[1].quantization == "Q4_K_M"
    assert artifacts[1].state is StoreState.VERIFIED
    assert artifacts[0].state is StoreState.DOWNLOADED
    assert artifacts[1].size_bytes == 16


def test_parse_artifacts_keeps_unknown_quantization_unobserved():
    document = _envelope([_artifact(quantization=None)])
    artifacts, error = parse_artifacts(document)
    assert error is None
    assert artifacts[0].quantization is None


def test_parse_artifacts_rejects_invalid_json():
    artifacts, error = parse_artifacts("{not json")
    assert artifacts == []
    assert error is not None


def test_parse_artifacts_rejects_foreign_schema():
    artifacts, error = parse_artifacts(
        json.dumps({"schema": "other", "payload": {}})
    )
    assert artifacts == []
    assert error is not None




def test_parse_store_payload_reads_the_store_core_selected():
    """The store path comes from Core's own ``store --json`` payload."""
    info, error = parse_store_payload(_store_envelope())
    assert error is None
    assert info is not None
    assert info.path == "/home/user/.local/share/castlearq/models"
    assert info.source == "default"
    assert info.exists is True


def test_parse_store_payload_rejects_a_foreign_schema():
    info, error = parse_store_payload(json.dumps({"schema": "other"}))
    assert info is None
    assert error is not None


def test_the_view_shows_the_store_core_selected(qapp):
    """The view names the store Core reported, never a guessed one."""
    view, _ = _view([FakeProcess(stdout=_envelope([_artifact()]))])
    view._on_refresh()
    assert "/home/user/.local/share/castlearq/models" in view._store_label.text()
    assert "default" in view._store_label.text()


def test_the_view_says_when_the_store_is_unknown(qapp):
    """Without a store report the view does not invent a path."""
    view, _ = _view([FakeProcess(stdout=_envelope([_artifact()]))])
    view._on_finished([], None)
    assert "no informado por el Core" in view._store_label.text()

def test_parse_artifacts_rejects_missing_payload():
    artifacts, error = parse_artifacts(
        json.dumps({"schema": "castlearq.cli", "schema_version": 1})
    )
    assert artifacts == []
    assert error is not None


def test_parse_artifacts_skips_unidentifiable_entries():
    document = _envelope(
        [
            _artifact(),
            {"model_id": "", "filename": "x.gguf", "status": "verified"},
            {"model_id": "m", "filename": "", "status": "verified"},
        ]
    )
    artifacts, error = parse_artifacts(document)
    assert error is None
    assert len(artifacts) == 1


def test_parse_artifacts_keeps_unknown_status_unrecognised():
    document = _envelope([_artifact(status="teleported")])
    artifacts, error = parse_artifacts(document)
    assert error is None
    assert artifacts[0].state is None


def test_read_helpers_are_public_and_consistent():
    entry = _artifact()
    assert read_quantization(entry) == "Q4_K_M"
    assert read_store_state(entry) is StoreState.VERIFIED
    assert read_store_state({"status": 7}) is None
    assert read_quantization({"quantization": "Q8_0"}) == "Q8_0"


def test_parse_artifacts_handles_the_real_core_output():
    """Regression: the captured output of the real Core CLI is parsed."""
    from pathlib import Path

    fixture = (
        Path(__file__).parent / "fixtures" / "core_list_real.json"
    )
    text = fixture.read_text(encoding="utf-8")
    artifacts, error = parse_artifacts(text)
    assert error is None
    assert len(artifacts) == 2
    by_model = {a.model_id: a for a in artifacts}
    qwen = by_model["qwen2.5-coder-7b-instruct"]
    assert qwen.filename == "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
    assert qwen.quantization == "Q4_K_M"
    assert qwen.state is StoreState.VERIFIED
    assert qwen.is_verified is True
    assert qwen.size_bytes == 4683073536
    tiny = by_model["thebloke-tinyllama-1-1b-chat-v1-0-gguf-2feea3bcf265"]
    assert tiny.filename == "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
    assert tiny.quantization == "Q4_K_M"
    assert tiny.state is StoreState.VERIFIED



# --- service behaviour -----------------------------------------------------


def test_refresh_uses_the_public_core_cli():
    service, popen = _service([FakeProcess(stdout=_envelope([_artifact()]))])
    finished: list = []
    service.finished.connect(lambda artifacts, store: finished.append(artifacts))
    assert service.refresh() is True
    assert popen.calls == [
        [PYTHON, "-m", "castlearq.main", "list", "--json"],
        [PYTHON, "-m", "castlearq.main", "store", "--json"],
    ]
    assert len(finished) == 1
    assert finished[0][0].filename == "demo.gguf"


def test_refresh_reports_empty_library():
    service, _ = _service([FakeProcess(stdout=_envelope([]))])
    finished: list = []
    service.finished.connect(lambda artifacts, store: finished.append(artifacts))
    service.refresh()
    assert finished == [[]]


def test_refresh_reports_invalid_json_as_failure():
    service, _ = _service([FakeProcess(stdout="not json at all")])
    failures: list[str] = []
    finished: list = []
    service.finished.connect(lambda artifacts, store: finished.append(artifacts))
    service.failed.connect(failures.append)
    service.refresh()
    assert finished == []
    assert len(failures) == 1
    assert failures[0]


def test_refresh_reports_process_error_as_failure():
    service, _ = _service([FakeProcess(stderr="boom", returncode=3)])
    failures: list[str] = []
    service.failed.connect(failures.append)
    service.refresh()
    assert len(failures) == 1
    assert "boom" in failures[0]


def test_refresh_reports_start_error_as_failure():
    class BoomPopen:
        def __call__(self, argv, **kwargs):
            raise FileNotFoundError("no such interpreter")

    service = LocalLibraryService(
        FakeSettings(),
        popen_factory=BoomPopen(),
        thread_pool_factory=ImmediatePool,
    )
    failures: list[str] = []
    service.failed.connect(failures.append)
    service.refresh()
    assert len(failures) == 1
    assert failures[0]


def test_consecutive_refreshes_report_the_latest_result():
    service, _ = _service(
        [
            FakeProcess(stdout=_envelope([_artifact(filename="old.gguf")])),
            FakeProcess(stdout=_envelope([_artifact(filename="new.gguf")])),
        ]
    )
    results: list = []
    service.finished.connect(lambda artifacts, store: results.append(artifacts))
    service.refresh()
    service.refresh()
    assert len(results) == 2
    assert results[0][0].filename == "old.gguf"
    assert results[1][0].filename == "new.gguf"


def test_second_refresh_while_busy_is_refused():
    service, _ = _service(
        [
            FakeProcess(stdout=_envelope([_artifact()])),
            FakeProcess(stdout=_envelope([_artifact()])),
        ]
    )
    assert service.refresh() is True
    # The synchronous pool finished the runnable, so the service is idle.
    assert service.is_busy is False
    assert service.refresh() is True


def test_stale_result_after_invalidate_is_ignored():
    service, _ = _service([FakeProcess(stdout=_envelope([_artifact()]))])
    finished: list = []
    service.finished.connect(lambda artifacts, store: finished.append(artifacts))
    service.refresh()
    assert len(finished) == 1  # the legitimate result
    service.invalidate()
    # A late delivery of an old generation must not be applied.
    service._on_resolved((1, [], None))
    assert len(finished) == 1
    assert finished[0][0].filename == "demo.gguf"


def test_close_prevents_further_queries():
    service, _ = _service([FakeProcess(stdout=_envelope([]))])
    service.close()
    assert service.refresh() is False


# --- view ------------------------------------------------------------------


def _view(scripts: list[FakeProcess]) -> tuple[LocalLibraryView, LocalLibraryService]:
    service, _ = _service(scripts)
    return LocalLibraryView(service=service), service


def test_view_starts_unqueried(qapp):
    view, _ = _view([])
    assert "Sin consultar" in view._status.text()
    assert view.artifacts == ()
    assert view.selected_artifact is None


def test_view_refresh_populates_the_real_artifacts(qapp):
    view, _ = _view([FakeProcess(stdout=_envelope([_artifact()]))])
    view._on_refresh()
    assert len(view.artifacts) == 1
    assert view.artifacts[0].filename == "demo.gguf"
    assert "1 artefacto(s)" in view._status.text()


def test_view_reports_empty_library(qapp):
    view, _ = _view([FakeProcess(stdout=_envelope([]))])
    view._on_refresh()
    assert view.artifacts == ()
    assert "vacía" in view._status.text()


def test_view_reports_query_failure(qapp):
    view, _ = _view([FakeProcess(stderr="boom", returncode=1)])
    view._on_refresh()
    assert "boom" in view._status.text()
    assert view.artifacts == ()


def test_view_selects_an_artifact(qapp):
    view, _ = _view([FakeProcess(stdout=_envelope([_artifact()]))])
    view._on_refresh()
    selected: list = []
    view.artifact_selected.connect(selected.append)
    view._list.setCurrentRow(0)
    assert len(selected) == 1
    assert selected[0].filename == "demo.gguf"
    assert view.selected_artifact is not None
    assert view.selected_artifact.model_id == "demo-model"
    assert "demo.gguf" in view._detail.text()


def test_view_reports_unverified_artifact(qapp):
    view, _ = _view(
        [FakeProcess(stdout=_envelope([_artifact(status="downloaded")]))]
    )
    view._on_refresh()
    view._list.setCurrentRow(0)
    assert "no está verificado" in view._detail.text()


def test_view_reports_verified_artifact(qapp):
    view, _ = _view([FakeProcess(stdout=_envelope([_artifact(status="verified")]))])
    view._on_refresh()
    view._list.setCurrentRow(0)
    assert "verificado por el Core" in view._detail.text()


def test_view_does_not_claim_verification_for_a_failed_artifact(qapp):
    view, _ = _view(
        [
            FakeProcess(
                stdout=_envelope(
                    [
                        _artifact(
                            status="failed",
                            problem="File size differs from manifest",
                        )
                    ]
                )
            )
        ]
    )
    view._on_refresh()
    view._list.setCurrentRow(0)
    assert "fallido" in view._detail.text()
    assert "no está verificado" in view._detail.text()
    assert "File size differs" in view._detail.text()


def test_view_invalidate_drops_pending_results(qapp):
    view, service = _view([FakeProcess(stdout=_envelope([_artifact()]))])
    view._on_refresh()
    view.invalidate()
    assert view.artifacts == ()
    assert "Sin consultar" in view._status.text()


def test_view_ignores_stale_results(qapp):
    view, service = _view([FakeProcess(stdout=_envelope([_artifact()]))])
    finished: list = []
    service.finished.connect(lambda artifacts, store: finished.append(artifacts))
    service.invalidate()
    service._on_resolved((99, [], None))
    assert finished == []


def test_view_lines_show_model_file_quantization_and_state(qapp):
    artifact = LocalArtifact(
        model_id="demo-model",
        filename="demo.gguf",
        quantization="Q4_K_M",
        size_bytes=16,
        state=StoreState.DOWNLOADED,
    )
    text = "\n".join(artifact_lines(artifact))
    assert "demo.gguf" in text
    assert "demo-model" in text
    assert "Q4_K_M" in text
    assert "descargado" in text


def test_store_state_text_covers_the_core_vocabulary():
    assert "verificado" in store_state_text(StoreState.VERIFIED)
    assert "descargado" in store_state_text(StoreState.DOWNLOADED)
    assert "descargando" in store_state_text(StoreState.DOWNLOADING)
    assert "fallido" in store_state_text(StoreState.FAILED)
    assert "no descargado" in store_state_text(StoreState.NOT_DOWNLOADED)
    assert store_state_text(None)


# --- independence of the three states -------------------------------------


def test_catalog_store_and_library_states_are_independent(qapp):
    """A catalog entry does not create a library entry, and vice versa."""
    from castlearq_desktop.core.catalog_controller import CatalogController
    from castlearq_desktop.ui.model_explorer_view import ModelExplorerView

    catalog = CatalogController(is_ready=lambda: True)
    explorer = ModelExplorerView(controller=catalog)
    view, _ = _view([FakeProcess(stdout=_envelope([_artifact()]))])
    view._on_refresh()

    # The library knows an artifact the catalog does not list at all.
    assert len(view.artifacts) == 1
    assert explorer.selected_entry is None
    # ... and the catalog is not modified by the library query.
    assert explorer._has_valid_catalog is False


def test_library_refresh_does_not_change_the_core_status_line(qapp):
    """The library reports its own state, never Core's status line."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    library = window._local_library
    library._on_failed("consulta fallida")
    assert "consulta fallida" in library._status.text()
    assert "Core:" not in library._status.text()


def test_acquisition_triggers_a_library_refresh(qapp):
    """A finished acquisition refreshes the library from the real store."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    service = window._local_library_service
    calls: list[int] = []
    def counting_refresh() -> bool:
        # The real query is not started: no process is spawned in tests.
        calls.append(1)
        return True

    service.refresh = counting_refresh
    window._on_acquisition_finished(object())
    assert len(calls) == 1


def test_acquisition_does_not_start_a_duplicate_library_query(qapp):
    """A second acquisition while a query runs does not start another one."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    service = window._local_library_service
    calls: list[int] = []
    def counting_refresh() -> bool:
        # The real query is not started: no process is spawned in tests.
        calls.append(1)
        return True

    service.refresh = counting_refresh
    service._active_generation = 5  # a query is already in flight
    window._on_acquisition_finished(object())
    assert calls == []
    service._active_generation = 0

def test_refresh_without_interpreter_fails():
    service = LocalLibraryService(
        FakeSettings(path=None),
        popen_factory=FakePopen([]),
        thread_pool_factory=ImmediatePool,
    )
    failures: list[str] = []
    service.failed.connect(failures.append)
    assert service.refresh() is False
    assert len(failures) == 1


def test_catalog_and_artifact_selections_do_not_overwrite_each_other(qapp):
    """The catalog selection and the artifact selection keep their own lines."""
    from castlearq_desktop.core.model_catalog import ModelEntry
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    entry = ModelEntry(
        model_id="demo-model",
        name="Demo Model",
        provider="local",
        family="demo",
        task="chat",
        architecture="llama",
    )
    window._on_model_selected(entry)
    window._on_artifact_selected(
        LocalArtifact(model_id="demo-model", filename="demo.gguf")
    )

    assert "Demo Model" in window._model_status_label.text()
    assert "demo.gguf" in window._artifact_status_label.text()

    # Clearing the artifact leaves the catalog selection untouched.
    window._on_artifact_unselected()
    assert "Demo Model" in window._model_status_label.text()
    assert "demo.gguf" not in window._artifact_status_label.text()


def test_selecting_a_catalog_model_without_a_session_keeps_chat_startable(qapp):
    """Regression: selecting a catalog model with no chat session open must
    not disable «Iniciar chat» when a valid artifact is already selected.

    ``MainWindow._on_model_selected`` routes through ``ChatView.close_session``
    unconditionally; closing without a session must be a harmless no-op on the
    ``ChatService`` (it previously left the service permanently busy).
    """
    from castlearq_desktop.core.model_catalog import ModelEntry
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    entry = ModelEntry(
        model_id="demo-model",
        name="Demo Model",
        provider="local",
        family="demo",
        task="chat",
        architecture="llama",
    )
    window._on_artifact_selected(
        LocalArtifact(
            model_id="demo-model",
            filename="demo.gguf",
            quantization="Q4_K_M",
            state=StoreState.VERIFIED,
        )
    )
    # Selecting in the library does not activate the artifact in Chat.
    assert window._chat_view._artifact is not None
    assert window._chat_view.has_session is False

    window._on_model_selected(entry)

    assert window._chat_service.session_id is None
    assert window._chat_service.is_busy is False
    assert window._chat_view._artifact is not None


# --- stable selection across refreshes --------------------------------------


def _installed(
    model_id: str,
    filename: str,
    quantization: Optional[str] = "Q4_K_M",
    state: StoreState = StoreState.VERIFIED,
) -> LocalArtifact:
    """One installed artifact as Core would report it."""
    return LocalArtifact(
        model_id=model_id,
        filename=filename,
        quantization=quantization,
        state=state,
    )


def _acquisition_result(
    status: AcquisitionStatus = AcquisitionStatus.READY,
    model_id: str = "modelo-x",
    filename: Optional[str] = "x.gguf",
) -> AcquisitionResult:
    """One acquisition outcome as the explorer reports it."""
    return AcquisitionResult(status=status, model_id=model_id, filename=filename)


def _selection_events(view) -> list:
    """Record every selection/deselection the view publishes."""
    events: list = []
    view.artifact_selected.connect(
        lambda artifact: events.append(("selected", artifact.filename))
    )
    view.artifact_unselected.connect(lambda: events.append(("unselected",)))
    return events


def _stub_refresh(window) -> list:
    """Replace the library refresh by a recorder (no process is spawned)."""
    started: list = []

    def counting_refresh() -> bool:
        started.append(True)
        return True

    window._local_library_service.refresh = counting_refresh
    return started


def _open_chat_session(window) -> None:
    """Open a real chat session on the window through a fake Core client."""
    from castlearq_desktop.core.chat_client import ChatSessionInfo, ChatTurnResult

    class _FakeChatClient:
        def create_session(self, model_id: str, **kwargs: Any) -> ChatSessionInfo:
            return ChatSessionInfo("s-1", model_id, "ready")

        def delete_session(self, session_id: str) -> None:
            return None

        def send_turn(self, session_id: str, prompt: str) -> ChatTurnResult:
            return ChatTurnResult(session_id, "ok", 1)

    service = window._chat_service
    service._client = _FakeChatClient()
    service._thread_pool_factory = ImmediatePool
    assert service.open_session("modelo-a") is True


def test_repopulate_keeps_the_selection_without_spurious_events(qapp):
    """Regresión 1: repoblar con A y B conserva A y no emite nada."""
    view, _ = _view([])
    a_downloaded = _installed(
        "modelo-a", "a.gguf", "Q4_K_M", StoreState.DOWNLOADED
    )
    b = _installed("modelo-b", "b.gguf", "Q5_K_M")
    events = _selection_events(view)

    view._on_finished([a_downloaded])
    view._list.setCurrentRow(0)
    assert events == [("selected", "a.gguf")]

    # The same logical artifact (fresh object, now verified) plus a new one.
    a_verified = _installed("modelo-a", "a.gguf", "Q4_K_M", StoreState.VERIFIED)
    view._on_finished([b, a_verified])

    selected = view.selected_artifact
    assert selected is not None
    assert (selected.model_id, selected.filename) == ("modelo-a", "a.gguf")
    assert selected.state is StoreState.VERIFIED  # detail shows fresh state
    assert events == [("selected", "a.gguf")]  # no spurious event at all
    assert view._list.currentItem() is not None
    assert view._list.currentItem().data(Qt.UserRole).filename == "a.gguf"
    assert view._list.currentRow() == 1  # restored even after moving rows
    assert "verificado" in view._detail.text()


def test_refresh_with_an_open_chat_session_keeps_conversation(qapp):
    """Regresión 2: refrescar con sesión abierta no interrumpe nada."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    lib = window._local_library
    a = _installed("modelo-a", "a.gguf")
    b = _installed("modelo-b", "b.gguf")
    lib._on_finished([a])
    lib._list.setCurrentRow(0)
    _open_chat_session(window)
    chat = window._chat_view
    chat._history.append("Tú: hola")
    history_before = chat._history.toPlainText()
    controls_before = (
        chat._start_button.isEnabled(),
        chat._send_button.isEnabled(),
        chat._prompt.isEnabled(),
    )
    events = _selection_events(lib)

    lib._on_finished(
        [_installed("modelo-a", "a.gguf", "Q4_K_M", StoreState.DOWNLOADED), b]
    )

    # Identity, session, history and controls are all preserved.
    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "a.gguf"
    assert events == []
    assert chat.has_session is True
    assert window._chat_service.session_id == "s-1"
    assert chat._history.toPlainText() == history_before
    assert (
        chat._start_button.isEnabled(),
        chat._send_button.isEnabled(),
        chat._prompt.isEnabled(),
    ) == controls_before
    assert "a.gguf" in window._artifact_status_label.text()
    # Selection does not activate Chat; the active artifact is unchanged.


def test_successful_acquisition_selects_the_downloaded_artifact(qapp):
    """Regresión 3: descarga exitosa → X seleccionado y chat iniciable."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    started = _stub_refresh(window)
    lib = window._local_library
    events: list = []
    lib.artifact_selected.connect(lambda art: events.append(art.filename))

    window._on_acquisition_finished(_acquisition_result())

    assert started == [True]
    assert lib._pending_selection == ("modelo-x", "x.gguf", None)
    # Core's results arrive and contain the downloaded artifact.
    lib._on_finished(
        [_installed("modelo-a", "a.gguf"), _installed("modelo-x", "x.gguf")],
        None,
    )

    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "x.gguf"
    assert events == ["x.gguf"]
    assert lib._list.currentItem() is not None
    assert lib._list.currentItem().data(Qt.UserRole).filename == "x.gguf"
    assert lib._list.currentRow() == 1  # made visible/current
    assert lib._pending_selection is None  # consumed exactly once
    assert "x.gguf" in window._artifact_status_label.text()
    chat = window._chat_view
    # Selection in the library does not activate Chat implicitly.
    assert chat._artifact is not None
    assert chat.has_session is False


def test_acquisition_with_an_active_session_forces_no_selection(qapp):
    """Regresión 4: con conversación abierta no se fuerza nada."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    started = _stub_refresh(window)
    lib = window._local_library
    a = _installed("modelo-a", "a.gguf")
    lib._on_finished([a])
    lib._list.setCurrentRow(0)
    _open_chat_session(window)
    chat = window._chat_view
    chat._history.append("Tú: hola")
    events = _selection_events(lib)

    window._on_acquisition_finished(_acquisition_result())  # success, x.gguf

    assert lib._pending_selection is None  # never scheduled
    assert started == [True]  # the refresh itself still runs
    lib._on_finished([a, _installed("modelo-x", "x.gguf")], None)

    assert events == []  # no forced selection of x.gguf
    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "a.gguf"
    assert chat.has_session is True
    assert window._chat_service.session_id == "s-1"
    assert "Tú: hola" in chat._history.toPlainText()
    assert chat._artifact is not None


def test_failed_acquisition_schedules_no_selection_and_keeps_current(qapp):
    """Regresión 5: descarga fallida → sin auto-selección, A se conserva."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    started = _stub_refresh(window)
    lib = window._local_library
    a = _installed("modelo-a", "a.gguf")
    b = _installed("modelo-b", "b.gguf")
    lib._on_finished([a])
    lib._list.setCurrentRow(0)
    events: list = []
    lib.artifact_selected.connect(lambda art: events.append(art.filename))

    window._on_acquisition_finished(
        _acquisition_result(status=AcquisitionStatus.FAILED)
    )

    assert lib._pending_selection is None  # nothing scheduled
    assert started == [True]  # existing behaviour: refresh still runs
    lib._on_finished([a, b], None)

    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "a.gguf"
    assert events == []  # no approximation to another artifact


def test_incomplete_or_absent_result_never_selects_by_approximation(qapp):
    """Regresión 6: identidad incompleta o resultado ausente → sin aproximar."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    started = _stub_refresh(window)
    lib = window._local_library
    a = _installed("modelo-a", "a.gguf")
    b = _installed("modelo-b", "b.gguf")
    lib._on_finished([a])
    lib._list.setCurrentRow(0)
    events: list = []
    lib.artifact_selected.connect(lambda art: events.append(art.filename))

    # (a) Incomplete identity (no filename): nothing is scheduled.
    window._on_acquisition_finished(
        _acquisition_result(model_id="modelo-x", filename=None)
    )
    assert lib._pending_selection is None
    lib._on_finished([a, b], None)
    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "a.gguf"

    # (b) Valid request but the artifact is absent from Core's results.
    window._on_acquisition_finished(
        _acquisition_result(model_id="modelo-y", filename="y.gguf")
    )
    assert lib._pending_selection == ("modelo-y", "y.gguf", None)
    lib._on_finished([a, b], None)
    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "a.gguf"  # kept, no approximation
    assert lib._pending_selection is None  # consumed, not lingering

    # A later, unrelated query must not re-apply the old request.
    lib._on_finished([a, b, _installed("modelo-y", "y.gguf")], None)
    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "a.gguf"
    assert events == []
    assert started == [True, True]


def test_removing_the_selected_artifact_emits_one_deselection(qapp):
    """Regresión 7: si la selección ya no existe, una única deselección."""
    view, _ = _view([])
    a = _installed("modelo-a", "a.gguf")
    b = _installed("modelo-b", "b.gguf")
    view._on_finished([a, b])
    view._list.setCurrentRow(0)
    events = _selection_events(view)

    view._on_finished([b])

    assert view.selected_artifact is None
    assert events == [("unselected",)]
    assert view._detail.text() == ""
    assert view._list.currentItem() is None


def test_acquisition_while_a_query_runs_keeps_the_pending_selection(qapp):
    """Regresión 8: consulta en curso → la selección pendiente no se pierde."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    started = _stub_refresh(window)
    window._local_library_service._active_generation = 5  # query in course
    lib = window._local_library

    window._on_acquisition_finished(_acquisition_result())

    assert started == []  # no query was started here (refresh not assumed)
    assert lib._pending_selection == ("modelo-x", "x.gguf", None)  # not lost
    assert lib.selected_artifact is None  # nothing applied yet

    # The query already in course delivers: the pending request applies.
    window._local_library_service._active_generation = 0
    lib._on_finished([_installed("modelo-x", "x.gguf")], None)

    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "x.gguf"
    assert lib._pending_selection is None  # consumed exactly once


def test_pending_selection_yields_to_a_session_opened_while_refreshing(qapp):
    """Regresión de carrera: la sesión abierta durante el refresco gana.

    Secuencia: sin sesión se programa la auto-selección de X y arranca el
    refresco; antes de que terminen los resultados, el usuario abre una
    sesión con A y ve historial; cuando la consulta devuelve X, X no debe
    reemplazar A ni interrumpir la conversación.
    """
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    started = _stub_refresh(window)
    lib = window._local_library
    a = _installed("modelo-a", "a.gguf")
    lib._on_finished([a])
    lib._list.setCurrentRow(0)  # chat target: A
    events = _selection_events(lib)

    # 1) No session: auto-selection of X is scheduled; the query starts.
    window._on_acquisition_finished(_acquisition_result())
    assert lib._pending_selection == ("modelo-x", "x.gguf", None)
    assert started == [True]

    # 2) Before the results arrive, the user opens a session on A.
    _open_chat_session(window)
    chat = window._chat_view
    chat._history.append("Tú: hola")
    history_before = chat._history.toPlainText()
    assert chat.has_session is True

    # 3) The query finishes and contains both A and the downloaded X.
    lib._on_finished([a, _installed("modelo-x", "x.gguf")], None)

    # X must not replace A and nothing may interrupt the conversation.
    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "a.gguf"
    assert chat._artifact is not None
    assert chat.has_session is True
    assert window._chat_service.session_id == "s-1"
    assert chat._history.toPlainText() == history_before
    assert chat._start_button.isEnabled() is False  # session owns the controls
    assert events == []  # no change/deselection signal was published
    # The pending request was consumed, not left to poison a later query.
    assert lib._pending_selection is None
    lib._on_finished([_installed("modelo-x", "x.gguf"), a], None)
    assert lib.selected_artifact.filename == "a.gguf"
    assert events == []
    assert chat.has_session is True


def test_pending_selection_applies_normally_without_a_session(qapp):
    """Control: sin sesión al terminar el refresco, X se selecciona."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    started = _stub_refresh(window)
    lib = window._local_library
    a = _installed("modelo-a", "a.gguf")
    lib._on_finished([a])
    lib._list.setCurrentRow(0)
    events = _selection_events(lib)

    window._on_acquisition_finished(_acquisition_result())
    assert started == [True]
    assert lib._pending_selection == ("modelo-x", "x.gguf", None)
    # No session is opened this time.
    assert window._chat_view.has_session is False

    lib._on_finished([a, _installed("modelo-x", "x.gguf")], None)

    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "x.gguf"
    assert events == [("selected", "x.gguf")]
    assert lib._pending_selection is None
    assert window._chat_view.has_session is False
    assert window._chat_view._artifact is not None


def test_library_finished_feeds_the_explorer_local_availability(qapp):
    """La señal finished entrega la lista completa al explorador.

    - El explorador recibe la colección entera (no solo la del modelo).
    - La disponibilidad se actualiza para el modelo seleccionado.
    - Un modelo ausente de la biblioteca se presenta como sin artefactos.
    """
    from castlearq_desktop.core.catalog_client import CatalogResult
    from castlearq_desktop.core.model_catalog import ModelEntry
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    explorer = window._model_explorer
    installed = ModelEntry(
        model_id="modelo-x",
        name="Modelo X",
        provider="p",
        family="f",
        task="t",
        architecture="a",
    )
    absent = ModelEntry(
        model_id="modelo-z",
        name="Modelo Z",
        provider="p",
        family="f",
        task="t",
        architecture="a",
    )
    explorer._on_finished(CatalogResult(models=(installed, absent)))
    explorer._on_item_clicked(explorer._list.item(0))  # select Modelo X
    assert explorer.selected_entry is not None
    # Nothing claimed before a correct library query exists.
    assert explorer._local_detail.text() == ""

    artifacts = [
        LocalArtifact(
            model_id="modelo-x",
            filename="x-Q4.gguf",
            quantization="Q4_K_M",
            state=StoreState.VERIFIED,
        ),
        LocalArtifact(
            model_id="modelo-x",
            filename="x-Q5.gguf",
            quantization="Q5_K_M",
            state=StoreState.DOWNLOADED,
        ),
        LocalArtifact(
            model_id="otro-modelo", filename="o.gguf", state=StoreState.VERIFIED
        ),
    ]
    # A correct query is delivered through the real service signal.
    window._local_library_service.finished.emit(artifacts, None)

    # The explorer received the complete collection...
    assert explorer._local_artifacts == tuple(artifacts)
    # ...and the section now reflects the selected model only.
    text = explorer._local_detail.text()
    assert "2 artefacto(s)" in text
    assert "x-Q4.gguf" in text and "x-Q5.gguf" in text
    assert "o.gguf" not in text

    # A model absent from the library is honestly presented as having none.
    explorer._on_item_clicked(explorer._list.item(1))
    assert "Sin artefactos locales para este modelo" in (
        explorer._local_detail.text()
    )


def _catalog_entry(model_id: str, name: str) -> Any:
    """One catalog entry for the model-explorer regression tests."""
    from castlearq_desktop.core.model_catalog import ModelEntry

    return ModelEntry(
        model_id=model_id,
        name=name,
        provider="p",
        family="f",
        task="t",
        architecture="a",
    )


def test_browsing_another_catalog_model_keeps_the_chat_session(qapp):
    """Explorar otro modelo del catálogo no cierra la conversación activa."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    lib = window._local_library
    a = _installed("modelo-a", "a.gguf")
    lib._on_finished([a])
    lib._list.setCurrentRow(0)
    _open_chat_session(window)
    chat = window._chat_view
    chat._history.append("Tú: hola")
    history_before = chat._history.toPlainText()
    controls_before = (
        chat._start_button.isEnabled(),
        chat._send_button.isEnabled(),
        chat._close_button.isEnabled(),
    )
    deletes: list = []
    service = window._chat_service
    service._client.delete_session = lambda session_id: deletes.append(session_id)

    window._on_model_selected(_catalog_entry("modelo-b", "Modelo B"))

    assert service.session_id == "s-1"
    assert chat.has_session is True
    assert chat._history.toPlainText() == history_before
    assert deletes == []  # la sesión de Core no se elimina
    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "a.gguf"
    assert chat._artifact is not None
    assert (
        chat._start_button.isEnabled(),
        chat._send_button.isEnabled(),
        chat._close_button.isEnabled(),
    ) == controls_before  # controles intactos, no rotura
    assert "Modelo B" in window._model_status_label.text()
    assert "a.gguf" in window._artifact_status_label.text()


def test_deselecting_a_catalog_model_keeps_the_chat_session(qapp):
    """Deseleccionar el modelo del catálogo tampoco interrumpe el chat."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    lib = window._local_library
    a = _installed("modelo-a", "a.gguf")
    lib._on_finished([a])
    lib._list.setCurrentRow(0)
    _open_chat_session(window)
    chat = window._chat_view
    chat._history.append("Tú: hola")
    history_before = chat._history.toPlainText()

    window._on_model_selected(_catalog_entry("modelo-b", "Modelo B"))
    window._on_model_unselected()

    assert window._chat_service.session_id == "s-1"
    assert chat.has_session is True
    assert chat._history.toPlainText() == history_before
    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "a.gguf"
    assert chat._artifact is not None


def test_selecting_same_model_id_as_the_chat_artifact_keeps_session(qapp):
    """El mismo model_id del artefacto del chat tampoco cierra la sesión."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    lib = window._local_library
    a = _installed("modelo-a", "a.gguf")
    lib._on_finished([a])
    lib._list.setCurrentRow(0)
    _open_chat_session(window)
    chat = window._chat_view
    chat._history.append("Tú: hola")
    history_before = chat._history.toPlainText()

    window._on_model_selected(_catalog_entry("modelo-a", "Modelo A"))

    assert window._chat_service.session_id == "s-1"
    assert chat.has_session is True
    assert chat._history.toPlainText() == history_before
    assert chat._artifact is not None


def test_catalog_browsing_without_session_keeps_chat_startable(qapp):
    """Sin sesión, explorar el catálogo no rompe ni bloquea el chat."""
    from castlearq_desktop.ui.main_window import MainWindow

    window = MainWindow()
    lib = window._local_library
    a = _installed("modelo-a", "a.gguf")
    lib._on_finished([a])
    lib._list.setCurrentRow(0)
    chat = window._chat_view
    assert chat.has_session is False
    assert chat._start_button.isEnabled() is True

    window._on_model_selected(_catalog_entry("modelo-b", "Modelo B"))

    assert window._chat_service.session_id is None
    assert chat.has_session is False
    assert chat._start_button.isEnabled() is True
    assert "Modelo B" in window._model_status_label.text()
    assert "a.gguf" in window._artifact_status_label.text()

    window._on_model_unselected()

    assert window._chat_service.session_id is None
    assert chat.has_session is False
    assert chat._start_button.isEnabled() is True
    assert lib.selected_artifact is not None
    assert lib.selected_artifact.filename == "a.gguf"
