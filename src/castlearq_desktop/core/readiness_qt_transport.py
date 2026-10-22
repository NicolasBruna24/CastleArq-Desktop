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

"""Qt-based asynchronous HTTP readiness transport (DEC-B / DEC-E).

Maps the previously synchronous readiness flow onto the Qt event loop so the
GUI stays responsive while readiness runs:

* ``QNetworkAccessManager`` issues non-blocking GET requests to the three
  approved endpoints (``/health``, ``/v1/models``, ``/v1/artifacts``).
* ``QTimer`` drives the per-request timeout (10 s, capped by the global 20 s
  deadline), the 250 ms polling interval, and the global deadline -- no
  ``time.sleep`` and no synchronous HTTP.
* Qt signals deliver each pass (``progress``) and the terminal outcome
  (``finished``) to the caller.
* No threads, executors, blocking waits or busy loops are introduced.

The transport is lifecycle-neutral: it cancels only its own network requests
and timers. It never touches the process, the LifecycleManager or the shutdown
policy. Like the synchronous DEC-E contract, a request that outlives the global
deadline is *not* preempted at the exact instant of expiry; the overrun is
detected and classified as ``READINESS_TIMEOUT`` instead of success.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, FrozenSet, List, Optional, Tuple

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

#: Logger dedicado al diagnóstico temporal de readiness (solo DEBUG).
_logger = logging.getLogger(__name__)

from castlearq_desktop.core.contracts import (
    ARTIFACTS_PATH,
    HEALTH_PATH,
    MODELS_PATH,
    POLL_INTERVAL_SECONDS,
    READINESS_REQUEST_TIMEOUT_SECONDS,
    Deadline,
    ErrorCode,
    ProbeObservation,
    ReadinessCondition,
    ReadinessObservations,
    VersionAdmission,
    evaluate_readiness,
)
from castlearq_desktop.core.readiness_client import (
    ReadinessProbeResult,
    ReadinessWaitResult,
)
from castlearq_desktop.core.version_admission import MAX_HEALTH_BODY_BYTES
from castlearq_desktop.core.version_admission import SUPPORTED_CORE_VERSIONS
from castlearq_desktop.core.version_admission import evaluate_version_admission

#: Approved probe order for one readiness pass (DEC-B).
READINESS_PROBE_PATHS = (HEALTH_PATH, MODELS_PATH, ARTIFACTS_PATH)

_NoError = QNetworkReply.NetworkError.NoError
_Canceled = QNetworkReply.NetworkError.OperationCanceledError
_ProtocolFailure = QNetworkReply.NetworkError.ProtocolFailure
_StatusAttr = QNetworkRequest.Attribute.HttpStatusCodeAttribute
_ContentLengthAttr = getattr(
    QNetworkRequest.Attribute, "ContentLengthAttribute",
    "Content-Length")


def _declared_content_length(reply: Any) -> Optional[int]:
    """Best-effort declared length; None when absent/unparseable (untrusted)."""
    try:
        raw = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
    except Exception:
        raw = None
    if raw is None and _ContentLengthAttr is not None:
        try:
            raw = reply.attribute(_ContentLengthAttr)
        except Exception:
            raw = None
    try:
        length = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return length if length >= 0 else None


def _stage_code(path: str) -> ErrorCode:
    if path == HEALTH_PATH:
        return ErrorCode.HEALTH_CHECK_FAILED
    return ErrorCode.API_PROBE_FAILED


def _is_success(kind: str, status: Optional[int]) -> bool:
    return kind == "ok" and status is not None and 200 <= int(status) <= 299

class QtReadinessTransport(QObject):
    """Asynchronous readiness engine over Qt Network (DEC-B / DEC-E).

    Emits :attr:`progress` after every pass and :attr:`finished` exactly once
    with a terminal :class:`ReadinessWaitResult`. See the module docstring for
    the temporal guarantees and limitations.
    """

    progress = Signal(object)
    finished = Signal(object)
    error = Signal(Exception)

    def __init__(
        self,
        base_url: str,
        *,
        child_observer: Callable[[], Optional[bool]],
        api_contract: VersionAdmission,
        clock: Any,
        timer_factory: Callable[[], Any],
        nam: Optional[Any] = None,
        installed_version_provider: Optional[Callable[[], Any]] = None,
        version_pending: Optional[Callable[[], bool]] = None,
        supported_versions: FrozenSet[str] = SUPPORTED_CORE_VERSIONS,
        request_timeout: float = READINESS_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__()
        self.base_url = base_url
        self._child_observer = child_observer
        self._api_contract = api_contract
        self._clock = clock
        self._timer_factory = timer_factory
        self._nam = nam if nam is not None else QNetworkAccessManager(self)
        self._installed_version_provider = installed_version_provider
        self._version_pending = version_pending
        self._supported_versions = supported_versions
        #: Per-request cap for readiness probes. Injectable so a test (or a
        #: future policy revision) can exercise another cap without changing
        #: the global budget.
        self._request_timeout = float(request_timeout)

        self._finished = False
        self._deadline: Optional[Deadline] = None
        self._passes = 0

        #: Wall-clock start time per in-flight request path (seconds, monotonic).
        self._request_start_times: dict = {}
        #: Measured duration per completed request path (seconds, monotonic).
        self._request_durations: dict = {}

        # True while a completed pass is waiting for the installed version.
        self._held = False

        # per-pass state
        self._alive_before: Optional[bool] = None
        self._active_replies: dict = {}
        self._pass_results: dict = {}
        self._request_timers: dict = {}

        self._poll_timer: Optional[Any] = None
        self._deadline_timer: Optional[Any] = None

    # -- public API -----------------------------------------------------
    def start(self, now: float, deadline: Optional[Deadline]) -> None:
        """Begin the asynchronous readiness flow (returns immediately)."""
        if self._finished:
            return
        self._deadline = deadline
        remaining = deadline.remaining(now)
        self._deadline_timer = self._make_timer(
            remaining, self._on_global_deadline)
        self._begin_pass(now)

    def stop(self) -> None:
        """Cancel the operation; finalize exactly once with a non-ready result."""
        self._held = False
        self._finish(self._cancelled_result())

    def resume(self) -> None:
        """Resume a held pass once the installed version has been delivered.

        Re-evaluates the responses retained from the held pass. It is a no-op
        when nothing is held or the operation already finished, so a late
        delivery can never turn a finished operation into ``READY``. The
        global deadline is not reset: it was fixed once in :meth:`start`.
        """
        if self._finished or not self._held:
            return
        self._held = False
        self._evaluate_pass()

    def _hold_pass(self) -> None:
        """Retain the pass responses and suspend the final evaluation.

        All three HTTP replies have arrived but the installed version is still
        pending. The responses stay in ``_pass_results``: no request is
        re-issued, no timer is armed and nothing is emitted. Holding never
        finalizes the operation — the global deadline timer keeps running, so
        a version that never arrives still ends in the existing timeout.
        """
        if self._finished:
            return
        self._held = True

    # -- timer helper ---------------------------------------------------
    def _make_timer(self, seconds: float, slot: Callable[[], None]) -> Any:
        timer = self._timer_factory()
        if hasattr(timer, "setSingleShot"):
            timer.setSingleShot(True)
        timer.setInterval(int(max(0.0, seconds) * 1000))
        timer.timeout.connect(slot)
        timer.start()
        return timer

    # -- pass lifecycle -------------------------------------------------
    def _begin_pass(self, now: float) -> None:
        if self._finished:
            return
        if self._deadline.is_expired(now):
            self._finish(self._timeout_result())
            return
        self._passes += 1
        self._active_replies = {}
        self._pass_results = {}
        self._request_timers = {}
        self._alive_before = self._observe()
        for path in READINESS_PROBE_PATHS:
            self._issue_request(path, now)

    def _issue_request(self, path: str, now: float) -> None:
        if self._finished:
            return
        if not self._deadline.may_start_request(now):
            self._pass_results[path] = ("not_started", None, None)
            self._check_complete()
            return
        timeout = self._deadline.effective_request_timeout(
            now, max_request_timeout=self._request_timeout)
        request = QNetworkRequest(QUrl(self._url(path)))
        #: Registra el instante de inicio para medir la duración real de la
        #: solicitud (incluidos los timeouts) sin alterar la petición.
        self._request_start_times[path] = self._now()
        try:
            reply = self._nam.get(request)
        except Exception:  # pragma: no cover - defensive
            self._pass_results[path] = ("transport_error", None, None)
            self._check_complete()
            return
        self._active_replies[path] = reply
        reply.finished.connect(
            lambda p=path, r=reply: self._on_reply_finished(p, r))
        self._request_timers[path] = self._make_timer(
            timeout, lambda p=path, r=reply: self._on_request_timeout(p, r))

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    def _on_reply_finished(self, path: str, reply: Any) -> None:
        timer = self._request_timers.pop(path, None)
        if timer is not None:
            self._stop_timer(timer)
        self._active_replies.pop(path, None)
        #: Mide la duración real de la solicitud (incluidos timeouts) antes de
        #: descartar el reply; None si la petición no llegó a iniciarse.
        start = self._request_start_times.pop(path, None)
        if start is not None:
            self._request_durations[path] = self._now() - start
        if self._finished:
            self._delete_reply(reply)
            return
        self._pass_results[path] = self._classify_reply(reply)
        self._delete_reply(reply)
        self._check_complete()

    def _classify_reply(self, reply: Any) -> Tuple[str, Optional[int], Any]:
        err = reply.error()
        status = reply.attribute(_StatusAttr)
        status = int(status) if status is not None else None
        body = self._read_reply_body(reply)
        if err == _NoError:
            return ("ok", status, body)
        if err == _Canceled:
            return ("timeout", None, None)
        if err == _ProtocolFailure and status is not None:
            return ("http_error", status, body)
        return ("transport_error", None, None)

    @staticmethod
    def _read_reply_body(reply: Any) -> Any:
        """Best-effort bounded read of a finished reply body (M2).

        Qt's ``QNetworkReply`` offers no pre-materialization byte cap, so a
        true streaming limit is not available with this API. Mitigation,
        without weakening fail-closed: (1) when the reply declares a
        ``Content-Length`` above ``MAX_HEALTH_BODY_BYTES``, skip the read
        entirely; (2) otherwise read once and discard anything above the
        cap. Oversize/empty/unreadable bodies therefore yield ``None``,
        which ``extract_health_version`` maps to absent and the admission
        policy maps to a non-admitted outcome. No blocking, no new threads.
        """
        try:
            declared = _declared_content_length(reply)
        except Exception:
            declared = None
        if declared is not None and declared > MAX_HEALTH_BODY_BYTES:
            try:
                reply.abort()
            except Exception:
                pass
            return None
        try:
            read_all = reply.readAll()
        except Exception:
            return None
        try:
            if hasattr(read_all, "data"):
                raw = bytes(read_all.data())
            else:
                raw = bytes(read_all)
        except Exception:
            return None
        if len(raw) > MAX_HEALTH_BODY_BYTES:
            return None
        return raw

    def _on_request_timeout(self, path: str, reply: Any) -> None:
        if self._finished:
            return
        if path in self._active_replies:
            try:
                reply.abort()  # triggers finished -> classified as timeout
            except Exception:
                pass


    def _log_pass(self, now: float, alive_after: Optional[bool], overrun: bool,
                  code: Optional[ErrorCode], ready: bool) -> None:
        """Registra el diagnóstico temporal de una pasada de readiness (solo DEBUG).

        No altera la lógica de readiness, los timeouts, las señales ni el ciclo
        de vida de Qt; solo lee valores ya disponibles y los emite al logger.
        """
        remaining = (
            self._deadline.remaining(now) if self._deadline is not None else None)
        parts: List[str] = []
        for path in READINESS_PROBE_PATHS:
            kind, status, _body = self._pass_results.get(
                path, ("not_started", None, None))
            duration = self._request_durations.get(path)
            parts.append(
                f"path={path} kind={kind} status={status} "
                f"duration_ms={None if duration is None else round(duration * 1000, 1)}")
        _logger.debug(
            "READINESS pass=%s %s remaining_s=%s child_before=%s child_after=%s "
            "overrun=%s code=%s ready=%s",
            self._passes, " ".join(parts),
            None if remaining is None else round(remaining, 3),
            self._to_predicate(self._alive_before), alive_after, overrun,
            code.value if code is not None else None, ready)

    def _check_complete(self) -> None:
        if self._finished or self._active_replies:
            return
        self._evaluate_pass()

    def _evaluate_pass(self) -> None:
        if self._version_is_pending():
            # Responses are complete but DEC-A cannot be evaluated yet. Hold
            # the pass instead of emitting a premature not-ready outcome.
            self._hold_pass()
            return
        now = self._now()
        alive_after = self._observe()
        overrun = self._deadline.is_expired(now)
        readiness = evaluate_readiness(ReadinessObservations(
            child_alive_before=self._to_predicate(self._alive_before),
            health=self._to_observation(HEALTH_PATH),
            models=self._to_observation(MODELS_PATH),
            artifacts=self._to_observation(ARTIFACTS_PATH),
            child_alive_after=self._to_predicate(alive_after),
            api_contract=self._resolve_contract(),
        ))
        failed = [(p, k) for p, (k, s, _b) in self._ordered_results()
                  if not _is_success(k, s)]
        code = self._classify(readiness, failed, overrun=overrun)
        ready = readiness.ready and not overrun
        self._log_pass(now, alive_after, overrun, code, ready)
        self.progress.emit(ReadinessProbeResult(
            ready=ready, outcome=readiness, error_code=code))
        if ready:
            _logger.debug(
                "READINESS FINAL pass=%s code=None deadline_expired=False ready=True",
                self._passes)
            self._finish(ReadinessWaitResult(
                ready=True, outcome=readiness, passes=self._passes,
                error_code=None, deadline_expired=False))
            return
        delay = self._deadline.next_poll_delay(now)
        if delay <= 0.0:
            self._finish(self._timeout_result())
            return
        self._poll_timer = self._make_timer(
            delay, lambda: self._begin_pass(self._now()))

    def _on_global_deadline(self) -> None:
        if self._finished:
            return
        self._finish(self._timeout_result())

    # -- helpers --------------------------------------------------------
    def _now(self) -> float:
        return float(self._clock.monotonic())

    def _version_is_pending(self) -> bool:
        """Whether the installed version is still being resolved.

        Only consulted when a probe seam was injected. A failing probe is
        treated as "not pending" so the pass is evaluated and DEC-A fails
        closed on the absent version rather than being held indefinitely.
        """
        if self._version_pending is None:
            return False
        try:
            return bool(self._version_pending())
        except Exception:
            return False

    def _observe(self) -> Optional[bool]:
        try:
            return self._child_observer()
        except Exception:
            return None

    @staticmethod
    def _to_predicate(value: Optional[bool]) -> bool:
        return value is True

    def _resolve_contract(self) -> VersionAdmission:
        """Resolve DEC-A admission for one pass (fail-closed)."""
        if self._installed_version_provider is None:
            return self._api_contract
        try:
            installed = self._installed_version_provider()
        except Exception:
            installed = None
        health_body = self._health_body()
        return evaluate_version_admission(
            installed, health_body,
            supported_versions=self._supported_versions)

    def _health_body(self) -> Any:
        entry = self._pass_results.get(HEALTH_PATH)
        if entry is None or len(entry) < 3:
            return None
        return entry[2]

    def _ordered_results(self) -> List[Tuple[str, Tuple[str, Optional[int], Any]]]:
        return [(p, self._pass_results.get(p, ("not_started", None, None)))
                for p in READINESS_PROBE_PATHS]

    def _to_observation(self, path: str) -> ProbeObservation:
        entry = self._pass_results.get(path, ("not_started", None, None))
        kind, status = entry[0], entry[1]
        if kind in ("ok", "http_error") and status is not None:
            return ProbeObservation(path=path, status=status)
        return ProbeObservation(path=path, status=None)

    def _classify(self, readiness, failed, *, overrun: bool):
        if readiness.ready and not overrun:
            return None
        if overrun:
            return ErrorCode.READINESS_TIMEOUT
        non_probe = [f for f in readiness.failures if f.condition in (
            ReadinessCondition.API_CONTRACT,
            ReadinessCondition.CHILD_ALIVE_BEFORE,
            ReadinessCondition.CHILD_ALIVE_AFTER)]
        if non_probe:
            return readiness.error_code()
        if len(failed) == 1:
            return _stage_code(failed[0][0])
        return None

    def _timeout_result(self) -> ReadinessWaitResult:
        # Fail-closed diagnostic note: timeout/cancellation/deadline paths
        # carry no HTTP observations, so there is no /health body to feed
        # the dynamic DEC-A evaluation; the static api_contract input is
        # reported as-is (production: NOT_EVALUATED, never ready).
        outcome = evaluate_readiness(ReadinessObservations(
            api_contract=self._api_contract))
        result = ReadinessWaitResult(
            ready=False, outcome=outcome, passes=self._passes,
            error_code=ErrorCode.READINESS_TIMEOUT, deadline_expired=True)
        _logger.debug(
            "READINESS FINAL pass=%s code=READINESS_TIMEOUT deadline_expired=True ready=False",
            self._passes)
        return result

    def _cancelled_result(self) -> ReadinessWaitResult:
        outcome = evaluate_readiness(ReadinessObservations(
            api_contract=self._api_contract))
        result = ReadinessWaitResult(
            ready=False, outcome=outcome, passes=self._passes,
            error_code=None, deadline_expired=False)
        _logger.debug(
            "READINESS FINAL pass=%s code=None deadline_expired=False ready=False cancelled=True",
            self._passes)
        return result

    def _finish(self, result: ReadinessWaitResult) -> None:
        if self._finished:
            return
        self._finished = True
        self._cleanup()
        self.finished.emit(result)

    def _cleanup(self) -> None:
        for timer in list(self._request_timers.values()):
            self._stop_timer(timer)
        self._request_timers = {}
        for reply in list(self._active_replies.values()):
            try:
                reply.abort()
            except Exception:
                pass
            self._delete_reply(reply)
        self._active_replies = {}
        self._request_start_times = {}
        self._request_durations = {}
        self._stop_timer(self._poll_timer)
        self._poll_timer = None
        self._stop_timer(self._deadline_timer)
        self._deadline_timer = None

    @staticmethod
    def _stop_timer(timer: Optional[Any]) -> None:
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass

    @staticmethod
    def _delete_reply(reply: Any) -> None:
        try:
            reply.deleteLater()
        except Exception:
            pass


class QtReadinessChecker(QObject):
    """Thin public wrapper delivering a single readiness result via signals.

    Owns a :class:`QtReadinessTransport` and forwards its terminal outcome
    exactly once through :attr:`result`, or an unexpected failure through
    :attr:`error`.
    """

    result = Signal(object)
    error = Signal(Exception)

    def __init__(self, transport: QtReadinessTransport) -> None:
        super().__init__()
        self._transport = transport
        self._emitted = False
        transport.finished.connect(self._on_finished)
        transport.error.connect(self._on_error)

    def start(self, now: float, deadline: Optional[Deadline]) -> None:
        self._transport.start(now, deadline)

    def stop(self) -> None:
        self._transport.stop()

    def _on_finished(self, result: object) -> None:
        if self._emitted:
            return
        self._emitted = True
        self.result.emit(result)

    def _on_error(self, exc: Exception) -> None:
        if self._emitted:
            return
        self._emitted = True
        self.error.emit(exc)


__all__ = [
    "QtReadinessTransport",
    "QtReadinessChecker",
    "READINESS_PROBE_PATHS",
]
