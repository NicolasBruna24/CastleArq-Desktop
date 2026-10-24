# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""One-shot Core CLI download driver for CastleArq Desktop.

The service runs the ratified CastleArq Core command

    python -m castlearq.main download <model-id> [--quantization Q] [--filename F] [--model-store PATH]

as a single, non-interactive child process. It is **discovery-free**: it never
asks Hugging Face for a manifest and it never imports Core's private
``model_store`` module. Its only job is to translate a stored ``ModelEntry`` into
that Core CLI invocation and to present the process's own text output and exit
code back to the UI.

Threading and invalidation follow the ratified Qt transport pattern already used
by ``catalog_qt_transport`` / ``version_probe``:

* the real ``subprocess.Popen``/``communicate`` runs on a ``QThreadPool`` worker,
  so the GUI thread is never blocked;
* at most one active acquisition (single-flight);
* a generation counter lets ``invalidate()`` drop results that arrive after Core
  shuts down or the window context changes.

Result verification (public boundary, no invention):

* A successful exit (``0``) is reported with the fields Core itself printed
  (``model_id``, ``filename``, ``destination``, ``manifest_path``).
* The local ``ModelStore`` is then consulted through Core's **public** CLI
  surface: ``python -m castlearq.main list --json``. That command is a
  ratified Core command (its JSON envelope is ``castlearq.cli`` v1) and it
  reports every artifact of the resolved store with its own state
  (``downloaded`` / ``verified`` / ...). Desktop therefore never imports
  Core's private modules and never inspects the store's files itself: the
  state it reports is Core's own state, read through a public interface.
* ``DOWNLOADED`` and ``VERIFIED`` are **not** equivalent. Desktop only says
  "verified" when Core's own store reports the ``verified`` state for that
  exact artifact; a ``downloaded`` artifact is reported as downloaded, and an
  artifact the store does not list is reported as not confirmed.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, Signal, QThreadPool

from castlearq_desktop.core.interpreter_settings import InterpreterSettingsStore

__all__ = [
    "AcquisitionStatus",
    "AcquisitionResult",
    "Request",
    "StoreState",
    "LocalArtifact",
    "StoreInfo",
    "ModelAcquisitionService",
    "LocalLibraryService",
    "build_download_argv",
    "build_list_argv",
    "build_store_argv",
    "parse_list_payload",
    "parse_artifacts",
    "parse_store_payload",
    "find_store_state",
    "read_quantization",
    "read_store_state",
]


class AcquisitionStatus(str, Enum):
    """Application-level states of a Core download attempt."""

    READY = "ready"  # transfer finished successfully
    ALREADY_DOWNLOADED = "already_downloaded"
    FAILED = "failed"
    USAGE = "usage"  # exit 2: incorrect invocation


class StoreState(str, Enum):
    """States Core's own store reports for a stored artifact.

    Mirrors Core's ``ArtifactState`` vocabulary, which is a public part of the
    ``castlearq list --json`` contract. ``DOWNLOADED`` and ``VERIFIED`` are
    distinct states and are never conflated.
    """

    NOT_DOWNLOADED = "not_downloaded"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    VERIFIED = "verified"
    FAILED = "failed"


@dataclass(frozen=True)
class Request:
    """A single download request, exactly as the UI asks for it."""

    model_id: str  # logical model id (also accepted as owner/name by Core)
    quantization: Optional[str] = None  # variant selector
    filename: Optional[str] = None  # variant selector (exact artifact filename)
    model_store: Optional[str] = None  # optional override of the Core store root


@dataclass(frozen=True)
class AcquisitionResult:
    """Result of one download attempt, carrying only what Core printed/returned."""

    status: AcquisitionStatus
    model_id: str
    # --- fields Core itself reports on stdout, when present ---
    filename: Optional[str] = None
    destination: Optional[str] = None
    size_bytes: Optional[str] = None
    manifest_path: Optional[str] = None
    # --- message / error ---
    message: Optional[str] = None
    # --- store state, read back through Core's public `list --json` ---
    store_state: Optional[StoreState] = None

    @property
    def succeeded(self) -> bool:
        return self.status in (
            AcquisitionStatus.READY,
            AcquisitionStatus.ALREADY_DOWNLOADED,
        )

    @property
    def store_confirmed(self) -> bool:
        """Whether Core's own store lists the artifact as present.

        ``True`` for a ``downloaded`` or a ``verified`` artifact. It is
        ``False`` when the store could not be consulted at all, so absence of
        confirmation is never presented as presence.
        """
        return self.store_state in (StoreState.DOWNLOADED, StoreState.VERIFIED)

    @property
    def store_verified(self) -> bool:
        """Whether Core's own store reports the ``verified`` state.

        Only Core may promote an artifact to ``verified``; Desktop never
        derives it from a successful transfer.
        """
        return self.store_state is StoreState.VERIFIED


_SIZED = "Size:"


def _require_absolute_interpreter(interpreter: str) -> None:
    if not isinstance(interpreter, str) or not interpreter.strip():
        raise ValueError("An interpreter path must be a non-empty string.")
    if not os.path.isabs(interpreter):
        raise ValueError(
            "An interpreter path must be absolute; no PATH lookup is performed."
        )


def build_download_argv(interpreter: str, request: Request) -> list[str]:
    """Build the Core CLI argv for a single download attempt.

    Raises ``ValueError`` for a non-absolute or empty interpreter path, matching
    the existing interpreter validation policy (``build_validation_command``).
    """
    _require_absolute_interpreter(interpreter)
    argv = [interpreter, "-m", "castlearq.main", "download", request.model_id]
    if request.quantization:
        argv += ["--quantization", request.quantization]
    if request.filename:
        argv += ["--filename", request.filename]
    if request.model_store:
        argv += ["--model-store", request.model_store]
    return argv


def build_list_argv(interpreter: str, model_store: Optional[str] = None) -> list[str]:
    """Build the Core CLI argv that reads the local store as JSON.

    ``list`` is a ratified Core command and ``--json`` is valid for it, so this
    is a public, read-only way to observe the store's own state.

    ``model_store`` is left unset on purpose: Core resolves the store for the
    invocation with its own documented precedence (``--model-store``, the
    ``[models]`` directory of ``config.toml``, ``XDG_DATA_HOME`` and the
    CastleArq default), and the download path resolves it the same way. Not
    passing it therefore keeps the library and the downloads on the very same
    store, and it never makes Desktop read Core's configuration.
    """
    _require_absolute_interpreter(interpreter)
    argv = [interpreter, "-m", "castlearq.main", "list", "--json"]
    if model_store:
        argv += ["--model-store", model_store]
    return argv


def build_store_argv(interpreter: str, model_store: Optional[str] = None) -> list[str]:
    """Build the Core CLI argv that reports the selected model store as JSON.

    ``store`` is a ratified Core command; its JSON payload names the store
    that Core actually resolved (``path``) and how it was chosen (``source``).
    """
    _require_absolute_interpreter(interpreter)
    argv = [interpreter, "-m", "castlearq.main", "store", "--json"]
    if model_store:
        argv += ["--model-store", model_store]
    return argv


def parse_list_payload(text: str) -> tuple[dict[str, list[dict[str, Any]]], Optional[str]]:
    """Parse a ``castlearq list --json`` document.

    Returns ``(artifacts_by_model_id, error)``. The document is the ratified
    ``castlearq.cli`` envelope, so the payload is read from ``payload`` and
    never guessed from free text. A malformed document yields an error string
    instead of an exception: the caller must be able to report "not confirmed"
    without failing the download that already succeeded.
    """
    try:
        document = json.loads(text)
    except (TypeError, ValueError) as exc:
        return {}, f"no se pudo leer el JSON del almacén local: {exc}"
    if not isinstance(document, dict):
        return {}, "el JSON del almacén local no es un objeto"
    if document.get("schema") != "castlearq.cli":
        return {}, "el JSON del almacén local no usa el esquema castlearq.cli"
    payload = document.get("payload")
    if not isinstance(payload, dict):
        return {}, "el JSON del almacén local no contiene un payload"
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return {}, "el JSON del almacén local no contiene artefactos"
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in artifacts:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            continue
        grouped.setdefault(model_id, []).append(entry)
    return grouped, None


def _quantization_value(raw: Any) -> Optional[str]:
    """Read a quantization from the CLI's known/unknown JSON structure."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict) and raw.get("status") == "known":
        value = raw.get("value")
        if isinstance(value, str):
            return value
    return None


def read_quantization(entry: dict[str, Any]) -> Optional[str]:
    """Read the quantization of one ``list --json`` artifact entry.

    Public because the local library needs the very same reading of Core's
    known/unknown structure, so the two surfaces cannot drift. An unobserved
    quantization (``status: "unknown"``) stays ``None``: it is never
    fabricated from the filename.
    """
    return _quantization_value(entry.get("quantization"))


def read_store_state(entry: dict[str, Any]) -> Optional[StoreState]:
    """Read Core's own state of one ``list --json`` artifact entry.

    Returns ``None`` when the entry carries no state or a state outside
    Core's own ``ArtifactState`` vocabulary, so an unknown value is never
    coerced into a state Desktop recognises.
    """
    raw_state = entry.get("status")
    if not isinstance(raw_state, str):
        return None
    try:
        return StoreState(raw_state)
    except ValueError:
        return None


def find_store_state(
    grouped: dict[str, list[dict[str, Any]]],
    model_id: str,
    filename: Optional[str] = None,
    quantization: Optional[str] = None,
) -> Optional[StoreState]:
    """Return Core's own state for the matching artifact, if the store lists it.

    The artifact is matched by ``filename`` when known, otherwise by
    ``quantization``. A model with several artifacts matches none of them
    unambiguously, so no state is claimed for it.
    """
    candidates = grouped.get(model_id)
    if not candidates:
        return None
    matches = candidates
    if filename:
        matches = [c for c in matches if c.get("filename") == filename]
        if not matches:
            return None
    elif quantization:
        matches = [
            c for c in matches if _quantization_value(c.get("quantization")) == quantization
        ]
        if not matches:
            return None
    elif len(matches) > 1:
        return None
    raw_state = matches[0].get("status")
    if not isinstance(raw_state, str):
        return None
    try:
        return StoreState(raw_state)
    except ValueError:
        return None


@dataclass(frozen=True)
class StoreInfo:
    """The model store Core itself resolved for the invocation.

    Every field comes from Core's ``store --json`` payload: ``path`` is the
    store directory Core selected and ``source`` names the rule that selected
    it. Desktop never resolves a store on its own and never guesses a path.
    """

    path: Optional[str] = None
    source: Optional[str] = None
    exists: Optional[bool] = None


def parse_store_payload(text: str) -> tuple[Optional[StoreInfo], Optional[str]]:
    """Parse a ``castlearq store --json`` document.

    Returns ``(store_info, error)``. The document is the ratified
    ``castlearq.cli`` envelope, so the payload is read from ``payload``.
    A missing or malformed document is reported as an error instead of
    raising: the library is still usable without knowing the store path.
    """
    try:
        document = json.loads(text)
    except (TypeError, ValueError) as exc:
        return None, f"no se pudo leer el JSON del almacén local: {exc}"
    if not isinstance(document, dict):
        return None, "el JSON del almacén local no es un objeto"
    if document.get("schema") != "castlearq.cli":
        return None, "el JSON del almacén local no usa el esquema castlearq.cli"
    payload = document.get("payload")
    if not isinstance(payload, dict):
        return None, "el JSON del almacén local no contiene un payload"
    path = payload.get("path")
    source = payload.get("source")
    exists = payload.get("exists")
    return (
        StoreInfo(
            path=path if isinstance(path, str) and path else None,
            source=source if isinstance(source, str) and source else None,
            exists=exists if isinstance(exists, bool) else None,
        ),
        None,
    )


@dataclass(frozen=True)
class LocalArtifact:
    """One artifact Core found in the local model store.

    Every field is something Core itself reported in ``list --json``:
    ``model_id`` and ``filename`` identify the artifact, ``quantization``
    and ``size_bytes`` describe it, ``state`` is Core's own state and
    ``problem`` is Core's own diagnostic. Nothing is inferred.
    """

    model_id: str
    filename: str
    quantization: Optional[str] = None
    size_bytes: Optional[int] = None
    state: Optional[StoreState] = None
    problem: Optional[str] = None

    @property
    def is_verified(self) -> bool:
        """Whether Core itself reports the artifact as verified."""
        return self.state is StoreState.VERIFIED

    @property
    def is_present(self) -> bool:
        """Whether Core reports the artifact as downloaded or verified."""
        return self.state in (StoreState.DOWNLOADED, StoreState.VERIFIED)


def parse_artifacts(text: str) -> tuple[list[LocalArtifact], Optional[str]]:
    """Parse a ``castlearq list --json`` document into local artifacts.

    Returns ``(artifacts, error)``. The document is the ratified
    ``castlearq.cli`` envelope, so the payload is read from ``payload`` and
    never guessed from free text. A malformed document yields an error
    string instead of an exception: the caller must be able to report
    "the library could not be consulted" without failing the query that
    already ran.

    Entries without a usable ``model_id`` or ``filename`` are skipped: an
    artifact Desktop cannot identify is never presented as a library entry.
    An unknown ``status`` value is preserved as ``None`` rather than being
    coerced into a state Core never reported.
    """
    grouped, error = parse_list_payload(text)
    if error is not None:
        return [], error
    artifacts: list[LocalArtifact] = []
    for entries in grouped.values():
        for entry in entries:
            model_id = entry.get("model_id")
            filename = entry.get("filename")
            if not isinstance(model_id, str) or not model_id:
                continue
            if not isinstance(filename, str) or not filename:
                continue
            size_bytes = entry.get("size_bytes")
            if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
                size_bytes = None
            problem = entry.get("problem")
            if not isinstance(problem, str) or not problem:
                problem = None
            artifacts.append(
                LocalArtifact(
                    model_id=model_id,
                    filename=filename,
                    quantization=read_quantization(entry),
                    size_bytes=size_bytes,
                    state=read_store_state(entry),
                    problem=problem,
                )
            )
    artifacts.sort(key=lambda a: (a.model_id, a.filename))
    return artifacts, None


@dataclass
class _ProcessOutcome:
    """Raw outcome of one child process run."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class _DownloadReport:
    """Everything Desktop learned from one download attempt."""

    result: AcquisitionResult
    model_store: Optional[str] = None
    lines: list[str] = field(default_factory=list)


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _value_after(lines: list[str], prefix: str) -> Optional[str]:
    """Return the value Core printed after *prefix* on some line."""
    for line in lines:
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            if value:
                return value
    return None


class _DownloadBridge(QObject):
    """Qt-side conduit delivering a worker result to the owning thread.

    ``QRunnable`` is not a ``QObject``, so the worker cannot own signals
    itself; the bridge carries them instead and re-emits them in the thread
    that owns it.
    """

    resolved = Signal(object)
    started = Signal()
    progress = Signal(str)
    failed = Signal(str)
    errored = Signal(str)

    def __init__(self) -> None:
        super().__init__()

    def deliver(self, payload: Any) -> None:
        """Hand a finished report back to the service's thread."""
        self.resolved.emit(payload)

    def announce_started(self) -> None:
        self.started.emit()

    def report_progress(self, line: str) -> None:
        self.progress.emit(line)

    def report_failure(self, message: str) -> None:
        self.failed.emit(message)

    def report_error(self, message: str) -> None:
        self.errored.emit(message)


class _DownloadRunnable(QRunnable):
    """Worker that runs the Core download CLI off the GUI thread."""

    def __init__(
        self,
        bridge: _DownloadBridge,
        generation: int,
        request: Request,
        popen_factory: Callable[..., Any],
        interpreter: str,
        core_working_directory: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._bridge = bridge
        self._generation = generation
        self._request = request
        self._popen_factory = popen_factory
        self._interpreter = interpreter
        self._core_working_directory = core_working_directory

        self.setAutoDelete(True)

    def run(self) -> None:
        self._bridge.announce_started()
        try:
            report = self._execute()
        except Exception as exc:  # noqa: BLE001
            self._bridge.report_error(f"No se pudo ejecutar la descarga: {exc}")
            return
        self._bridge.deliver((self._generation, report))

    # -- internals ---------------------------------------------------------

    def _execute(self) -> _DownloadReport:
        argv = build_download_argv(self._interpreter, self._request)
        outcome = self._run(argv, self._bridge.report_progress)
        lines = _split_lines(outcome.stdout)
        stderr_lines = _split_lines(outcome.stderr)
        if outcome.exit_code == 2:
            message = (
                stderr_lines[-1]
                if stderr_lines
                else (lines[-1] if lines else "línea de comandos inválida")
            )
            return _DownloadReport(
                result=AcquisitionResult(
                    status=AcquisitionStatus.USAGE,
                    model_id=self._request.model_id,
                    message=f"Uso incorrecto: {message}",
                ),
                model_store=self._request.model_store,
                lines=lines,
            )
        if outcome.exit_code != 0:
            message = (
                stderr_lines[-1]
                if stderr_lines
                else (lines[-1] if lines else "la descarga falló")
            )
            return _DownloadReport(
                result=AcquisitionResult(
                    status=AcquisitionStatus.FAILED,
                    model_id=self._request.model_id,
                    message=message,
                ),
                model_store=self._request.model_store,
                lines=lines,
            )
        report = self._interpret_success(lines)
        report.model_store = self._request.model_store
        report.lines = lines
        return report

    def _interpret_success(self, lines: list[str]) -> _DownloadReport:
        model_id = self._request.model_id
        filename = _value_after(lines, "Artifact:")
        artifact_model = _value_after(lines, "Model:")
        if artifact_model:
            model_id = artifact_model
        destination = _value_after(lines, "Destination:")
        size_bytes = _value_after(lines, _SIZED)
        manifest_path = _value_after(lines, "Registered manifest:")
        already = any("already downloaded" in line.lower() for line in lines)
        status = (
            AcquisitionStatus.ALREADY_DOWNLOADED
            if already
            else AcquisitionStatus.READY
        )
        message = (
            "El artefacto ya estaba descargado (informe del Core)."
            if already
            else "Descarga completada (informe del Core)."
        )
        return _DownloadReport(
            result=AcquisitionResult(
                status=status,
                model_id=model_id,
                filename=filename,
                destination=destination,
                size_bytes=size_bytes,
                manifest_path=manifest_path,
                message=message,
            )
        )

    def _run(
        self, argv: list[str], on_line: Callable[[str], None]
    ) -> _ProcessOutcome:
        try:
            process = self._popen_factory(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise RuntimeError(f"no se pudo iniciar Core: {exc}") from exc
        stdout, stderr = process.communicate()
        exit_code = process.returncode
        if exit_code is None:
            exit_code = process.poll()
        if exit_code is None:
            exit_code = -1
        for line in _split_lines(stdout or ""):
            on_line(line)
        return _ProcessOutcome(
            exit_code=int(exit_code), stdout=stdout or "", stderr=stderr or ""
        )


class ModelAcquisitionService(QObject):
    """Single-flight driver for the Core download CLI, with Qt-friendly signals."""

    started = Signal()
    progress = Signal(str)
    finished = Signal(AcquisitionResult)
    failed = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        interpreter_settings: InterpreterSettingsStore,
        *,
        popen_factory: Optional[Any] = None,
        thread_pool_factory: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._interpreter_settings = interpreter_settings
        self._popen_factory = (
            popen_factory if popen_factory is not None else subprocess.Popen
        )
        self._thread_pool_factory = (
            thread_pool_factory
            if thread_pool_factory is not None
            else QThreadPool.globalInstance
        )
        self._generation = 0
        self._active_generation = 0
        self._active_request: Optional[Request] = None
        self._bridge: Optional[_DownloadBridge] = None
        self._shutting_down = False

    # -- public API --------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        """Whether an acquisition attempt is currently active."""
        return self._active_generation > 0

    def request_download(self, request: Request) -> bool:
        """Start a download attempt. Returns False while another attempt is active."""
        if self._active_generation > 0:
            return False
        if self._shutting_down:
            return False

        try:
            python = self._selected_interpreter()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc) or "No interpreter configured.")
            return False
        if not isinstance(python, str) or not python.strip() or not os.path.isabs(python):
            self.error.emit(
                "The interpreter must be an absolute path; no PATH lookup is performed."
            )
            return False

        self._generation += 1
        self._active_generation = self._generation
        self._active_request = request
        self._shutting_down = False

        bridge = _DownloadBridge()
        runnable = _DownloadRunnable(
            bridge,
            self._generation,
            request,
            self._popen_factory,
            python,
        )
        bridge.started.connect(self._on_worker_started)
        bridge.progress.connect(self.progress)
        bridge.resolved.connect(self._on_resolved)
        bridge.failed.connect(self._on_failed)
        bridge.errored.connect(self._on_error)
        self._bridge = bridge
        self._thread_pool_factory().start(runnable)
        return True

    def invalidate(self) -> None:
        """Drop an in-flight result and release the bridge (Core shutdown / window close)."""
        self._active_generation = 0
        self._active_request = None
        self._bridge = None

    def close(self) -> None:
        """Terminate the active attempt and prevent new requests."""
        self._shutting_down = True
        self.invalidate()

    # -- internal handlers -------------------------------------------------

    def _on_worker_started(self) -> None:
        self.started.emit()

    def _on_resolved(self, payload: Any) -> None:
        """Receive a finished report from the worker thread."""
        try:
            generation, report = payload
        except (TypeError, ValueError):
            return
        self._on_finished(generation, report)

    def _on_finished(self, generation: int, report: _DownloadReport) -> None:
        if generation != self._active_generation:
            return  # stale result after invalidation
        result = report.result
        if result.status in (
            AcquisitionStatus.READY,
            AcquisitionStatus.ALREADY_DOWNLOADED,
        ):
            quantization = (
                self._active_request.quantization
                if self._active_request is not None
                else None
            )
            self._active_generation = 0
            self._active_request = None
            self._bridge = None
            self._apply_store_state(report, result, quantization)
            self.finished.emit(result)
        elif result.status == AcquisitionStatus.FAILED:
            self.failed.emit(result.message or "La descarga falló.")
        elif result.status == AcquisitionStatus.USAGE:
            self.failed.emit(result.message or "Uso incorrecto.")
        else:
            self.error.emit(result.message or "Resultado inesperado.")

    def _apply_store_state(
        self,
        report: _DownloadReport,
        result: AcquisitionResult,
        quantization: Optional[str],
    ) -> None:
        """Read the store's own state through Core's public ``list --json``.

        A failure to consult the store never fails the download: the result
        simply keeps ``store_state = None``, and the UI then reports the
        artifact as not confirmed rather than as verified.
        """
        try:
            python = self._selected_interpreter()
        except Exception:  # noqa: BLE001
            return
        try:
            argv = build_list_argv(python, report.model_store)
            outcome = self._run_list(argv)
        except Exception:  # noqa: BLE001
            return
        if outcome.exit_code != 0:
            return
        grouped, error = parse_list_payload(outcome.stdout)
        if error is not None:
            return
        state = find_store_state(
            grouped,
            result.model_id,
            filename=result.filename,
            quantization=quantization,
        )
        if state is None:
            return
        object.__setattr__(result, "store_state", state)
    def _run_list(self, argv: list[str]) -> _ProcessOutcome:
        process = self._popen_factory(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate()
        exit_code = process.returncode
        if exit_code is None:
            exit_code = process.poll()
        if exit_code is None:
            exit_code = -1
        return _ProcessOutcome(
            exit_code=int(exit_code), stdout=stdout or "", stderr=stderr or ""
        )

    def _on_failed(self, message: str) -> None:
        if self._active_generation == 0:
            return
        self._active_generation = 0
        self._active_request = None
        self._bridge = None
        self.failed.emit(message)

    def _on_error(self, message: str) -> None:
        if self._active_generation == 0:
            return
        self._active_generation = 0
        self._active_request = None
        self._bridge = None
        self.error.emit(message)

    def _selected_interpreter(self) -> str:
        value = self._interpreter_settings.load()
        if value is None:
            raise ValueError("No interpreter configured.")
        return str(value)



class LocalLibraryService(QObject):
    """Single-flight async reader of the local model store.

    The local library is read through Core's **public** CLI surface
    (``castlearq list --json``), never by importing Core's private modules
    and never by reading the store's files directly. Only the artifacts
    Core itself found in the storage are reported, so the library reflects
    the real store and not the catalog of downloadable models.

    The query runs on a ``QThreadPool`` worker, so the GUI thread is never
    blocked; a generation counter lets ``invalidate()`` drop results that
    arrive after the view is closed or the request is superseded.
    """

    started = Signal()
    # ``list`` (not ``object``): a plain ``object`` signal expands a Python
    # list into separate arguments instead of delivering it as one value.
    finished = Signal(list, object)  # list[LocalArtifact], Optional[StoreInfo]
    failed = Signal(str)  # user message

    def __init__(
        self,
        interpreter_settings: InterpreterSettingsStore,
        *,
        core_working_directory: Optional[str] = None,
        popen_factory: Optional[Any] = None,
        thread_pool_factory: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._interpreter_settings = interpreter_settings
        self._core_working_directory = core_working_directory
        self._popen_factory = (
            popen_factory if popen_factory is not None else subprocess.Popen
        )
        self._thread_pool_factory = (
            thread_pool_factory
            if thread_pool_factory is not None
            else QThreadPool.globalInstance
        )
        self._generation = 0
        self._active_generation = 0
        self._bridge: Optional[_LibraryBridge] = None
        self._shutting_down = False

    @property
    def is_busy(self) -> bool:
        """Whether a library query is currently active."""
        return self._active_generation > 0

    def refresh(self) -> bool:
        """Start one library query. Returns False while another one is active."""
        if self._active_generation > 0:
            return False
        if self._shutting_down:
            return False
        try:
            python = self._selected_interpreter()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or "No interpreter configured.")
            return False
        if not isinstance(python, str) or not python.strip() or not os.path.isabs(python):
            self.failed.emit(
                "The interpreter must be an absolute path; no PATH lookup is performed."
            )
            return False

        self._generation += 1
        self._active_generation = self._generation
        previous = self._bridge
        if previous is not None:
            # A superseded bridge must not survive: it is a QObject and
            # deleting it releases the pending worker's delivery target.
            previous.deleteLater()
        bridge = _LibraryBridge()
        # The bridge is owned by this service so that a late worker delivery
        # never hits a destroyed signal source.
        bridge.setParent(self)
        runnable = _LibraryRunnable(
            bridge,
            self._generation,
            self._popen_factory,
            python,
            core_working_directory=self._core_working_directory,
        )
        bridge.started.connect(self._on_worker_started)
        bridge.resolved.connect(self._on_resolved)
        bridge.failed.connect(self._on_failed)
        self._bridge = bridge
        self._thread_pool_factory().start(runnable)
        return True

    def invalidate(self) -> None:
        """Drop an in-flight result (view closed / request superseded)."""
        self._active_generation = 0
        self._bridge = None

    def close(self) -> None:
        """Stop accepting queries and release the active one."""
        self._shutting_down = True
        self.invalidate()

    # -- internal handlers -------------------------------------------------

    def _on_worker_started(self) -> None:
        self.started.emit()

    def _on_resolved(self, payload: Any) -> None:
        try:
            generation, artifacts, store, error = payload
        except (TypeError, ValueError):
            return
        if generation != self._active_generation:
            return  # stale result after invalidation
        self._active_generation = 0
        self._bridge = None
        if error is not None:
            self.failed.emit(error)
        else:
            self.finished.emit(artifacts, store)

    def _on_failed(self, message: str) -> None:
        if self._active_generation == 0:
            return
        self._active_generation = 0
        self._bridge = None
        self.failed.emit(message)

    def _selected_interpreter(self) -> str:
        value = self._interpreter_settings.load()
        if value is None:
            raise ValueError("No interpreter configured.")
        return str(value)


class _LibraryBridge(QObject):
    """Qt-side conduit delivering a worker result to the owning thread."""

    resolved = Signal(object)
    started = Signal()
    failed = Signal(str)

    def deliver(self, payload: Any) -> None:
        """Hand a finished result back to the service's thread."""
        self.resolved.emit(payload)

    def announce_started(self) -> None:
        self.started.emit()

    def report_failure(self, message: str) -> None:
        self.failed.emit(message)


class _LibraryRunnable(QRunnable):
    """Worker that reads the local store through Core's public CLI."""

    def __init__(
        self,
        bridge: _LibraryBridge,
        generation: int,
        popen_factory: Callable[..., Any],
        interpreter: str,
        core_working_directory: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._bridge = bridge
        self._generation = generation
        self._popen_factory = popen_factory
        self._interpreter = interpreter
        self._core_working_directory = core_working_directory
        self.setAutoDelete(True)

    def run(self) -> None:
        self._bridge.announce_started()
        try:
            artifacts, store, error = self._execute()
        except Exception as exc:  # noqa: BLE001
            self._bridge.report_failure(
                f"No se pudo consultar la biblioteca local: {exc}"
            )
            return
        self._bridge.deliver((self._generation, artifacts, store, error))

    def _execute(
        self,
    ) -> tuple[list[LocalArtifact], Optional[StoreInfo], Optional[str]]:
        artifacts, error = self._query(build_list_argv(self._interpreter))
        if error is not None:
            return [], None, error
        # The store path is informative only: a failure here must not hide the
        # artifacts that were read successfully.
        store, store_error = self._query_store()
        if store_error is not None:
            store = None
        return artifacts, store, None

    def _query_store(self) -> tuple[Optional[StoreInfo], Optional[str]]:
        stdout, stderr, exit_code = self._run(build_store_argv(self._interpreter))
        if exit_code != 0:
            return None, "no se pudo identificar el almacén local"
        return parse_store_payload(stdout or "")

    def _query(self, argv: list[str]) -> tuple[list[LocalArtifact], Optional[str]]:
        stdout, stderr, exit_code = self._run(argv)
        if exit_code != 0:
            detail = _split_lines(stderr or "")
            message = (
                detail[-1]
                if detail
                else f"la consulta terminó con el código {exit_code}"
            )
            return [], f"No se pudo consultar la biblioteca local: {message}"
        artifacts, error = parse_artifacts(stdout or "")
        if error is not None:
            return [], error
        return artifacts, None

    def _run(self, argv: list[str]) -> tuple[str, str, int]:
        # Determine the working directory: use configured core_working_directory
        # if set and valid, otherwise fall back to None (inherited CWD).
        cwd: Optional[str] = None
        if self._core_working_directory is not None:
            if os.path.isdir(self._core_working_directory):
                cwd = self._core_working_directory
            else:
                raise RuntimeError(
                    f"El directorio de trabajo de Core no existe o no es un directorio: "
                    f"{self._core_working_directory}"
                )
        try:
            process = self._popen_factory(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise RuntimeError(f"no se pudo iniciar Core: {exc}") from exc
        stdout, stderr = process.communicate()
        exit_code = process.returncode
        if exit_code is None:
            exit_code = process.poll()
        if exit_code is None:
            exit_code = -1
        return stdout or "", stderr or "", exit_code
