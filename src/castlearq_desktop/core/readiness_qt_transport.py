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
* ``QTimer`` drives the per-request timeout (4 s, capped by the global 20 s
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

from typing import Any, Callable, List, Optional, Tuple

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from castlearq_desktop.core.contracts import (
    ARTIFACTS_PATH,
    HEALTH_PATH,
    MAX_REQUEST_TIMEOUT_SECONDS,
    MODELS_PATH,
    POLL_INTERVAL_SECONDS,
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

#: Approved probe order for one readiness pass (DEC-B).
READINESS_PROBE_PATHS = (HEALTH_PATH, MODELS_PATH, ARTIFACTS_PATH)

_NoError = QNetworkReply.NetworkError.NoError
_Canceled = QNetworkReply.NetworkError.OperationCanceledError
_ProtocolFailure = QNetworkReply.NetworkError.ProtocolFailure
_StatusAttr = QNetworkRequest.Attribute.HttpStatusCodeAttribute


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
    ) -> None:
        super().__init__()
        self.base_url = base_url
        self._child_observer = child_observer
        self._api_contract = api_contract
        self._clock = clock
        self._timer_factory = timer_factory
        self._nam = nam if nam is not None else QNetworkAccessManager(self)

        self._finished = False
        self._deadline: Optional[Deadline] = None
        self._passes = 0

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
        self._finish(self._cancelled_result())

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
            self._pass_results[path] = ("not_started", None)
            self._check_complete()
            return
        timeout = self._deadline.effective_request_timeout(now)
        request = QNetworkRequest(QUrl(self._url(path)))
        try:
            reply = self._nam.get(request)
        except Exception:  # pragma: no cover - defensive
            self._pass_results[path] = ("transport_error", None)
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
        if self._finished:
            self._delete_reply(reply)
            return
        self._pass_results[path] = self._classify_reply(reply)
        self._delete_reply(reply)
        self._check_complete()

    def _classify_reply(self, reply: Any) -> Tuple[str, Optional[int]]:
        err = reply.error()
        status = reply.attribute(_StatusAttr)
        status = int(status) if status is not None else None
        if err == _NoError:
            return ("ok", status)
        if err == _Canceled:
            return ("timeout", None)
        if err == _ProtocolFailure and status is not None:
            return ("http_error", status)
        return ("transport_error", None)

    def _on_request_timeout(self, path: str, reply: Any) -> None:
        if self._finished:
            return
        if path in self._active_replies:
            try:
                reply.abort()  # triggers finished -> classified as timeout
            except Exception:
                pass


    def _check_complete(self) -> None:
        if self._finished or self._active_replies:
            return
        self._evaluate_pass()

    def _evaluate_pass(self) -> None:
        now = self._now()
        alive_after = self._observe()
        overrun = self._deadline.is_expired(now)
        readiness = evaluate_readiness(ReadinessObservations(
            child_alive_before=self._to_predicate(self._alive_before),
            health=self._to_observation(HEALTH_PATH),
            models=self._to_observation(MODELS_PATH),
            artifacts=self._to_observation(ARTIFACTS_PATH),
            child_alive_after=self._to_predicate(alive_after),
            api_contract=self._api_contract,
        ))
        failed = [(p, k) for p, (k, s) in self._ordered_results()
                  if not _is_success(k, s)]
        code = self._classify(readiness, failed, overrun=overrun)
        ready = readiness.ready and not overrun
        self.progress.emit(ReadinessProbeResult(
            ready=ready, outcome=readiness, error_code=code))
        if ready:
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

    def _observe(self) -> Optional[bool]:
        try:
            return self._child_observer()
        except Exception:
            return None

    @staticmethod
    def _to_predicate(value: Optional[bool]) -> bool:
        return value is True

    def _ordered_results(self) -> List[Tuple[str, Tuple[str, Optional[int]]]]:
        return [(p, self._pass_results.get(p, ("not_started", None)))
                for p in READINESS_PROBE_PATHS]

    def _to_observation(self, path: str) -> ProbeObservation:
        kind, status = self._pass_results.get(path, ("not_started", None))
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
        outcome = evaluate_readiness(ReadinessObservations(
            api_contract=self._api_contract))
        return ReadinessWaitResult(
            ready=False, outcome=outcome, passes=self._passes,
            error_code=ErrorCode.READINESS_TIMEOUT, deadline_expired=True)

    def _cancelled_result(self) -> ReadinessWaitResult:
        outcome = evaluate_readiness(ReadinessObservations(
            api_contract=self._api_contract))
        return ReadinessWaitResult(
            ready=False, outcome=outcome, passes=self._passes,
            error_code=None, deadline_expired=False)

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
