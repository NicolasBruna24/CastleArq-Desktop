# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

"""Asynchronous OpenVINO conversion driver for CastleArq Desktop.

This module implements a single, non-blocking conversion service that drives
CastleArq Core through its public ``castlearq.main convert`` CLI surface::

    # Hugging Face
    <interpreter> -m castlearq.main convert <model_id> --quantization <precision> --json

    # Local folder
    <interpreter> -m castlearq.main convert <artifact_id> --source-path <local_path> --quantization <precision> --json

The service is deliberately thin. It translates a Desktop conversion request
into those argv, runs the configured interpreter/CLI off the GUI thread through
``QThreadPool``, and then confirms that Core registered the resulting artifact
by reading the local library through the ratified public surface
``castlearq list --json``.

It does **not** perform the conversion itself, does not write or store
artefacts, does not configure Core, and it never imports Core's private
modules or touches its storage.

Signals
-------
* ``completed = Signal(object)`` carries a :class:`ConversionResult`. The
  ``success`` field distinguishes success from failure, so the observer always
  receives one object and never has to guess which signal fired.
* ``busy_changed = Signal(bool)`` reports the conversion state.

Concurrency and lifecycle
-------------------------
* At most one conversion is active per service instance (single-flight).
* The interpreter is loaded from :class:`InterpreterSettingsStore` and is
  validated as an absolute path; there is no silent ``PATH`` fallback.
* ``CONVERSION_TIMEOUT_SECONDS`` bounds the child process. On timeout the
  process is killed and the conversion is reported as failed.
* ``invalidate()`` drops an in-flight conversion (window close / Core shutdown)
  without pretending to cancel the actual Core command.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from castlearq_desktop.core.download_service import (
    LocalArtifact,
    StoreState,
    build_list_argv,
    parse_artifacts,
)
from castlearq_desktop.core.interpreter_settings import (
    InterpreterSettingsStore,
)

__all__ = [
    "CONVERSION_TIMEOUT_SECONDS",
    "CONVERSION_LIBRARY_QUERY_TIMEOUT_SECONDS",
    "CONVERSION_SUCCESS_MESSAGE",
    "ConversionRequest",
    "ConversionResult",
    "OpenVINOConversionService",
]

#: Wall-clock bound for the ``convert`` child process (seconds).
CONVERSION_TIMEOUT_SECONDS = 600.0

#: Bound for the ``list --json`` probe used to confirm the artefact.
CONVERSION_LIBRARY_QUERY_TIMEOUT_SECONDS = 30.0

#: User-facing message carried by a successful result.
CONVERSION_SUCCESS_MESSAGE = "Conversión completada y artefacto registrado."

#: Valid ``--quantization`` values accepted by Desktop for OpenVINO conversion.
VALID_PRECISIONS = ("fp16", "int8", "int4")

#: Stable, user-facing error codes carried by :class:`ConversionResult`.
ERROR_NO_INTERPRETER_CONFIGURED = "INTERPRETER_NOT_CONFIGURED"
ERROR_INVALID_INPUT = "INVALID_INPUT"
ERROR_PROCESS_START_FAILED = "PROCESS_START_FAILED"
ERROR_CONVERSION_TIMEOUT = "CONVERSION_TIMEOUT"
ERROR_INVALID_JSON = "INVALID_JSON"
ERROR_CONVERSION_FAILED = "CONVERSION_FAILED"
ERROR_ARTIFACT_NOT_REGISTERED = "ARTIFACT_NOT_REGISTERED"


class ConversionError(Exception):
    """A conversion-service failure that is reported through :attr:`completed`."""

    def __init__(
        self,
        user_message: str,
        *,
        error_code: Optional[str] = None,
        detail: Optional[str] = None,
        **context: Any,
    ) -> None:
        self.user_message: str = user_message
        self.error_code: Optional[str] = error_code
        self.detail: Optional[str] = detail
        self.context: dict[str, Any] = context
        super().__init__(user_message)


class ConversionValidationError(ConversionError):
    """Invalid request parameters (no process is started)."""


class ProcessStartError(ConversionError):
    """The child process could not be spawned."""


class ConversionTimeoutError(ConversionError):
    """The conversion child process exceeded the bounded timeout."""


@dataclass(frozen=True)
class ConversionRequest:
    """A single conversion request, as the UI asks for it."""

    modality: str  # "huggingface" | "local"
    artifact_id: str  # HF model id, or local artifact id
    source_path: Optional[str] = None  # required when modality == "local"
    precision: str = "fp16"  # fp16 | int8 | int4


@dataclass(frozen=True)
class ConversionResult:
    """Outcome of a conversion attempt, delivered on :attr:`OpenVINOConversionService.completed`."""

    success: bool
    message: str
    artifact: Optional[LocalArtifact] = None
    detail: Optional[str] = None
    error_code: Optional[str] = None
def _last_non_empty(text: str) -> str:
    """Return the last non-empty line of *text*, or an empty string."""
    for line in reversed((text or "").splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _first_lines(text: str, limit: int = 5) -> Optional[str]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return None
    return " | ".join(lines[:limit])


def _build_conversion_argv(
    interpreter: str, request: ConversionRequest
) -> list[str]:
    """Build the Core CLI argv for a single conversion attempt.

    Raises :class:`ConversionValidationError` for a non-absolute, empty or
    missing interpreter, an unknown modality, a missing ``source_path`` for a
    local request, or an unknown precision.
    """
    if not isinstance(interpreter, str) or not interpreter.strip():
        raise ConversionValidationError("No hay un intérprete de Core configurado.")
    if not os.path.isabs(interpreter):
        raise ConversionValidationError(
            "El intérprete debe ser una ruta absoluta; no se realiza búsqueda por PATH."
        )
    precision = _validate_precision(request.precision)
    modality = (request.modality or "").strip().lower()
    artifact_id = request.artifact_id or ""
    if not artifact_id.strip():
        raise ConversionValidationError("El identificador del artefacto es obligatorio.")

    if modality == "huggingface":
        return [
            interpreter,
            "-m",
            "castlearq.main",
            "convert",
            artifact_id.strip(),
            "--quantization",
            precision,
            "--json",
        ]
    if modality == "local":
        source_path = request.source_path
        if not source_path or not str(source_path).strip():
            raise ConversionValidationError(
                "Para la modalidad local se requiere ``source_path``."
            )
        return [
            interpreter,
            "-m",
            "castlearq.main",
            "convert",
            artifact_id.strip(),
            "--source-path",
            str(source_path).strip(),
            "--quantization",
            precision,
            "--json",
        ]

    raise ConversionValidationError(
        f"Modalidad no reconocida: {request.modality!r}. "
        "Usa ``huggingface`` o ``local``."
    )



def _validate_precision(precision: Any) -> str:
    """Normalise and validate the requested precision."""
    value = str(precision).strip().lower() if precision is not None else ""
    if value not in VALID_PRECISIONS:
        raise ConversionValidationError(
            f"Precisión no válida. Usa uno de: {', '.join(VALID_PRECISIONS)}."
        )
    return value


def _parse_conversion_payload(text: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Parse one ``castlearq.main convert --json`` envelope.

    Returns ``(payload, error)``. The document is a ratified
    ``castlearq.cli`` v1 envelope, so the payload is read from ``payload``.
    Malformed or non-zero-status documents yield ``(None, error_message)``
    instead of raising, so the caller can report failures without crashing.
    """
    try:
        document = json.loads(text)
    except (TypeError, ValueError) as exc:
        return None, f"El sobre JSON de Core no es válido: {exc}"
    if not isinstance(document, dict):
        return None, "El sobre JSON de Core no es un objeto"
    if document.get("schema") != "castlearq.cli":
        return None, "El sobre JSON de Core no usa el esquema castlearq.cli"
    exit_code = document.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return None, "El sobre JSON de Core no informa un código de salida entero"
    if exit_code != 0:
        return None, "La conversión de Core finalizó con un código de salida distinto de cero"
    payload = document.get("payload")
    if not isinstance(payload, dict):
        return None, "El sobre JSON de Core no contiene un payload"
    return payload, None


def _extract_conversion_identity(payload: dict[str, Any]) -> dict[str, Any]:
    """Read the artefact identity Core reports for a conversion payload.

    The conversion JSON is a ``castlearq.cli`` envelope; this helper reads the
    fields that identify the newly registered artefact, trying the most
    plausible keys. The result is deliberately permissive: missing keys are
    simply absent, and callers must treat an ambiguous or incomplete identity
    as a failure rather than inventing a match.
    """
    model_id = (
        payload.get("model_id")
        or payload.get("output_model_id")
        or payload.get("artifact_model_id")
        or payload.get("source_model_id")
    )
    filename = (
        payload.get("artifact_filename")
        or payload.get("filename")
        or payload.get("output_filename")
        or payload.get("artifact_name")
    )
    quantization = (
        payload.get("quantization")
        or payload.get("precision")
        or payload.get("quantization_format")
        or payload.get("target_precision")
    )
    return {
        "model_id": model_id,
        "filename": filename,
        "quantization": quantization,
        "backend": payload.get("backend"),
    }


def _find_matching_artifact(
    artifacts: list[LocalArtifact], identity: dict[str, Any]
) -> Optional[LocalArtifact]:
    """Return the uniquely matched OpenVINO artefact, or ``None``.

    Candidates are matched by ``model_id`` (required) and ``filename`` (when the
    conversion payload reports one). Because two distinct conversions may share a
    ``model_id``/``filename`` pair, the matched artefact must additionally be
    reported by Core with the ``openvino`` backend: anything else (``gguf`` or
    absent) is left unmatched. Several matching artefacts are treated as
    ambiguous and return ``None`` rather than an arbitrary pick.
    """
    model_id = identity.get("model_id")
    if not model_id:
        return None
    filename = identity.get("filename")
    candidates = [a for a in artifacts if a.model_id == model_id]
    if not candidates:
        return None
    if filename:
        candidates = [a for a in candidates if a.filename == filename]
        if not candidates:
            return None
    # The confirmed artefact must be an OpenVINO artefact.
    candidates = [a for a in candidates if a.backend == "openvino"]
    if len(candidates) != 1:
        return None
    return candidates[0]

def _run(
    argv: list[str],
    popen_factory: Callable[..., Any],
    *,
    timeout: float,
) -> tuple[str, str, int]:
    """Run a Core CLI argv and return ``(stdout, stderr, exit_code)``.

    ``shell`` is always ``False`` and argument lists are never joined for a
    shell. On timeout the child is killed and drained before the error is
    raised.
    """
    try:
        process = popen_factory(
            list(argv),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise ProcessStartError(
            f"No se pudo iniciar Core: {exc}",
            error_code=ERROR_PROCESS_START_FAILED,
            cause_type=type(exc).__name__,
        ) from exc

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Fail closed: kill and drain the child before reporting the timeout.
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            stdout, stderr = process.communicate()
        except Exception:  # noqa: BLE001
            stdout, stderr = "", ""
        raise ConversionTimeoutError(
            "La conversión superó el tiempo de espera.",
            error_code=ERROR_CONVERSION_TIMEOUT,
            timeout_seconds=timeout,
        ) from None

    exit_code = process.returncode
    if exit_code is None:
        exit_code = process.poll() or -1
    return stdout or "", stderr or "", exit_code


class _ConversionBridge(QObject):
    """Qt-side conduit delivering the final result to the owning thread."""

    finished = Signal(object)

    def deliver(self, payload: Any) -> None:
        self.finished.emit(payload)

class _ConversionRunnable(QRunnable):
    """Worker that executes a conversion off the GUI thread."""

    def __init__(
        self,
        bridge: "_ConversionBridge",
        generation: int,
        service: "OpenVINOConversionService",
        popen_factory: Callable[..., Any],
        interpreter: str,
        argv: list[str],
    ) -> None:
        super().__init__()
        self._bridge = bridge
        self._generation = generation
        self._service = service
        self._popen_factory = popen_factory
        self._interpreter = interpreter
        self._argv = argv
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self._execute()
        except ConversionError as exc:
            result = ConversionResult(
                success=False,
                message=exc.user_message,
                error_code=exc.error_code,
                detail=exc.detail,
            )
        except Exception as exc:  # noqa: BLE001 - defensive
            result = ConversionResult(
                success=False,
                message=str(exc),
                error_code=ERROR_CONVERSION_FAILED,
            )
        # Hand the result (and this worker's generation) back to the service's
        # thread so the slot can drop it if `invalidate()` superseded it.
        self._bridge.finished.emit((self._generation, result))

    # -- internals ---------------------------------------------------------

    def _execute(self) -> ConversionResult:
        """Run the conversion and confirm the artefact in the local library.

        Uses the interpreter/argv/popen captured at construction time, so an
        :meth:`OpenVINOConversionService.invalidate` that clears the service's
        own pending state cannot corrupt an already-queued worker.
        """
        argv = self._argv
        if argv is None:
            raise ConversionError("El servicio no tiene una conversión pendiente.")
        stdout, stderr, exit_code = _run(
            argv, self._popen_factory, timeout=CONVERSION_TIMEOUT_SECONDS
        )
        if exit_code != 0:
            detail = _last_non_empty(stderr) or _last_non_empty(stdout) or f"código de salida {exit_code}"
            raise ConversionError(detail, error_code=ERROR_CONVERSION_FAILED)

        payload, error = _parse_conversion_payload(stdout)
        if error is not None:
            raise ConversionError(error, error_code=ERROR_INVALID_JSON)

        identity = _extract_conversion_identity(payload)
        artifact = self._confirm_artifact(identity)
        if artifact is None:
            raise ConversionError(
                "El artefacto convertado no aparece registrado en la biblioteca local de Core.",
                error_code=ERROR_ARTIFACT_NOT_REGISTERED,
            )

        return ConversionResult(
            success=True,
            message=CONVERSION_SUCCESS_MESSAGE,
            artifact=artifact,
            detail=_first_lines(stdout, 5),
        )

    def _confirm_artifact(self, identity: dict[str, Any]) -> Optional[LocalArtifact]:
        """Query the local library and confirm the artefact is registered."""
        argv = build_list_argv(self._interpreter)
        stdout, stderr, exit_code = _run(
            argv,
            self._popen_factory,
            timeout=CONVERSION_LIBRARY_QUERY_TIMEOUT_SECONDS,
        )
        if exit_code != 0:
            return None
        artifacts, error = parse_artifacts(stdout or "")
        if error is not None:
            return None
        return _find_matching_artifact(artifacts, identity)

class OpenVINOConversionService(QObject):
    """Drives one OpenVINO conversion at a time, off the GUI thread."""

    #: Delivers the final :class:`ConversionResult` (success or failure).
    completed = Signal(object)
    #: ``True`` while a conversion is active, ``False`` when it finishes.
    busy_changed = Signal(bool)

    def __init__(
        self,
        interpreter_settings: InterpreterSettingsStore,
        *,
        popen_factory: Optional[Callable[..., Any]] = None,
        thread_pool_factory: Optional[Callable[[], QThreadPool]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._interpreter_settings = interpreter_settings
        self._popen_factory = (
            popen_factory if popen_factory is not None else subprocess.Popen
        )
        self._thread_pool_factory = (
            thread_pool_factory if thread_pool_factory is not None else QThreadPool.globalInstance
        )
        self._generation = 0
        self._active_generation = 0
        self._worker_running = False
        self._bridge: Optional[_ConversionBridge] = None
        self._interpreter: Optional[str] = None
        self._argv: Optional[list[str]] = None
        self._request: Optional[ConversionRequest] = None

    @property
    def is_busy(self) -> bool:
        """Whether a conversion is currently in flight."""
        return self._worker_running

    def convert(self, request: ConversionRequest) -> bool:
        """Start one conversion.

        Returns ``True`` when a worker was queued, ``False`` when the request
        was rejected (invalid parameters, interpreter not configured/invalid,
        or another conversion already in flight). Failures are delivered through
        :attr:`completed` with ``success=False``.
        """
        if not isinstance(request, ConversionRequest):
            self.completed.emit(
                ConversionResult(
                    success=False,
                    message="Una conversión requiere un objeto ConversionRequest.",
                    error_code=ERROR_INVALID_INPUT,
                )
            )
            return False

        # Resolve the explicitly configured interpreter (absolute path, no PATH
        # lookup). This is reported distinctly from an invalid request so the UI
        # can prompt the user to (re)select an interpreter rather than re-enter
        # conversion parameters.
        try:
            interpreter = self._configured_interpreter()
        except ConversionValidationError as exc:
            self.completed.emit(
                ConversionResult(
                    success=False,
                    message=str(exc),
                    error_code=ERROR_NO_INTERPRETER_CONFIGURED,
                )
            )
            return False

        # Validate the conversion parameters before starting any process.
        try:
            argv = _build_conversion_argv(interpreter, request)
        except ConversionValidationError as exc:
            self.completed.emit(
                ConversionResult(success=False, message=str(exc), error_code=ERROR_INVALID_INPUT)
            )
            return False

        # Reject a second concurrent conversion (single-flight).
        if self._worker_running:
            self.completed.emit(
                ConversionResult(
                    success=False,
                    message="Ya hay una conversión en curso. Espera a que termine la actual.",
                    error_code=ERROR_INVALID_INPUT,
                )
            )
            return False

        self._request = request
        self._interpreter = interpreter
        self._argv = argv
        self._generation += 1
        self._active_generation = self._generation
        self._worker_running = True

        self.busy_changed.emit(True)

        bridge = _ConversionBridge()
        runnable = _ConversionRunnable(
            bridge,
            self._generation,
            self,
            self._popen_factory,
            interpreter,
            argv,
        )
        bridge.finished.connect(self._on_finished)
        self._bridge = bridge
        self._thread_pool_factory().start(runnable)
        return True

    def invalidate(self) -> None:
        """Drop an in-flight conversion (window close / Core shutdown).

        This only cancels the outstanding result; it does not terminate the
        Core process that may already be running.
        """
        self._active_generation = 0
        self._bridge = None
        self._request = None
        self._argv = None

    def _configured_interpreter(self) -> str:
        """Return the explicitly configured (absolute) interpreter, or raise."""
        value = self._interpreter_settings.load()
        if value is None:
            raise ConversionValidationError("No hay un intérprete de Core configurado.")
        path = str(value)
        if not path.strip():
            raise ConversionValidationError("No hay un intérprete de Core configurado.")
        if not os.path.isabs(path):
            raise ConversionValidationError(
                "El intérprete debe ser una ruta absoluta; no se realiza búsqueda por PATH."
            )
        return path

    def _on_finished(self, payload: Any) -> None:
        """Receive a finished report from the worker thread and deliver it.

        The worker tags its result with the generation it was started under; a
        result whose generation does not match the still-active generation was
        superseded by :meth:`invalidate` (window close / Core shutdown) and is
        dropped before it reaches the UI.
        """
        generation, result = payload
        stale = generation != self._active_generation
        if not stale:
            self._active_generation = 0
            self._bridge = None
            self._request = None
            self._argv = None
            self.completed.emit(result)
            self.busy_changed.emit(False)
        self._worker_running = False
