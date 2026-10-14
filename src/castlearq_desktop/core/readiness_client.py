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

"""Readiness HTTP client for CastleArq Core (DEC-B / DEC-E).

Probes the three approved endpoints (``/health``, ``/v1/models``,
``/v1/artifacts``) against the fixed loopback base URL and classifies the
result with the existing pure readiness/error/deadline contracts.

Transport is an injectable callable returning ``(status, body)``. The
default transport uses stdlib ``urllib``: ``httpx`` is NOT declared in
``pyproject.toml`` and new dependencies are not authorized.

Liveness is an explicit injectable observation, never a default: pass
``child_observer`` (a zero-argument callable returning ``True`` (alive),
``False`` (exited), or ``None`` (unknown)) or the explicit booleans
``child_alive_before/after``. ``None`` (unknown) is fail-closed for
readiness and carries no invented error code. Observation is read-only:
no process is started, signalled, or terminated.

API-contract admission (DEC-A) has no approved parser: admission is an
explicit ``VersionAdmission`` input (default NOT_EVALUATED, fail-closed).
Bodies are never parsed; DEC-B success means observed 2xx only.

Temporal guarantee (honest): each request is started only with
``timeout=min(4 s, remaining)`` and the deadline is additionally checked
*after* each request via the clock, so an overrun is detected and reported
as ``READINESS_TIMEOUT`` instead of success. However ``urllib`` CANNOT
preempt a blocked socket at the exact global deadline: a single request
may still return after the deadline (bounded by its own effective
timeout). The global deadline therefore bounds sleeps and request starts,
and detects overruns — it does not interrupt a blocked ``urlopen``.
No threads/executors are introduced to work around this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional
from urllib import request as _urllib_request
from urllib.error import HTTPError as _UrllibHTTPError
from urllib.error import URLError as _UrllibURLError

from castlearq_desktop.core.contracts import ARTIFACTS_PATH
from castlearq_desktop.core.contracts import CORE_HOST
from castlearq_desktop.core.contracts import CORE_PORT
from castlearq_desktop.core.contracts import HEALTH_PATH
from castlearq_desktop.core.contracts import MODELS_PATH
from castlearq_desktop.core.contracts import Deadline
from castlearq_desktop.core.contracts import ErrorCode
from castlearq_desktop.core.contracts import MAX_REQUEST_TIMEOUT_SECONDS
from castlearq_desktop.core.contracts import POLL_INTERVAL_SECONDS
from castlearq_desktop.core.contracts import TRANSPORT_RETRIES_ENABLED
from castlearq_desktop.core.contracts import ProbeObservation
from castlearq_desktop.core.contracts import ReadinessCondition
from castlearq_desktop.core.contracts import ReadinessObservations
from castlearq_desktop.core.contracts import ReadinessOutcome
from castlearq_desktop.core.contracts import VersionAdmission
from castlearq_desktop.core.contracts import evaluate_readiness
from castlearq_desktop.core.version_admission import MAX_HEALTH_BODY_BYTES
from castlearq_desktop.core.version_admission import SUPPORTED_CORE_VERSIONS
from castlearq_desktop.core.version_admission import evaluate_version_admission
from castlearq_desktop.core.contracts import is_http_success

#: Approved probe order for one readiness pass.
READINESS_PROBE_PATHS = (HEALTH_PATH, MODELS_PATH, ARTIFACTS_PATH)


def default_base_url() -> str:
    """Return the fixed loopback base URL (DEC-G constants, not literals)."""
    return f"http://{CORE_HOST}:{CORE_PORT}"


@dataclass(frozen=True)
class HttpProbeResponse:
    """Minimal transport result: observed status plus opaque body."""

    status: Optional[int] = None
    body: Any = None


@dataclass(frozen=True)
class ProbeAttempt:
    """One probe attempt record: single transport call, no retries."""

    path: str
    timeout: float
    status: Optional[int] = None
    timed_out: bool = False
    transport_error: bool = False

@dataclass(frozen=True)
class _ProbePreparation:
    path: Optional[str]
    status: Optional[int]
    body: Any
    alive_before: Optional[bool]


@dataclass(frozen=True)
class ReadinessProbeResult:
    """Outcome of one synchronous probe pass over the three endpoints."""

    ready: bool
    outcome: ReadinessOutcome
    attempts: tuple = ()
    error_code: Optional[ErrorCode] = None


@dataclass(frozen=True)
class ReadinessWaitResult:
    """Outcome of deadline-bounded polling across one or more passes."""

    ready: bool
    outcome: ReadinessOutcome
    passes: int = 0
    error_code: Optional[ErrorCode] = None
    deadline_expired: bool = False


class HttpTransportError(Exception):
    """Transport-level failure (connection refused, DNS, socket error)."""


class HttpRequestTimeout(Exception):
    """A single request exceeded its effective timeout."""


def urllib_transport(base_url: str) -> Callable[..., HttpProbeResponse]:
    """Build the default stdlib transport bound to *base_url*."""

    def _get(path: str, *, timeout: float) -> HttpProbeResponse:
        url = base_url.rstrip("/") + path
        try:
            with _urllib_request.urlopen(url, timeout=max(0.0, float(timeout))) as resp:
                try:
                    raw = resp.read(MAX_HEALTH_BODY_BYTES + 1)
                except Exception:
                    raw = None
                return HttpProbeResponse(status=int(resp.status), body=raw)
        except _UrllibHTTPError as exc:
            try:
                raw = exc.read(MAX_HEALTH_BODY_BYTES + 1)
            except Exception:
                raw = None
            return HttpProbeResponse(status=int(exc.code), body=raw)
        except Exception as exc:
            name = type(exc).__name__
            if isinstance(exc, TimeoutError) or name in (
                    "TimeoutError", "socket.timeout", "Timeout"):
                raise HttpRequestTimeout(
                    str(exc) or "request timed out") from exc
            raise HttpTransportError(str(exc) or "transport failed") from exc

    return _get

class ReadinessHttpClient:
    """Synchronous readiness prober with injected transport/clock/sleeper."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        transport: Optional[Callable[..., HttpProbeResponse]] = None,
        clock: Optional[Any] = None,
        sleeper: Optional[Callable[[float], None]] = None,
        api_contract: VersionAdmission = VersionAdmission.NOT_EVALUATED,
        child_observer: Optional[Callable[[], Optional[bool]]] = None,
        child_alive_before: Optional[bool] = None,
        child_alive_after: Optional[bool] = None,
        installed_version_provider: Optional[Callable[[], Any]] = None,
        supported_versions: FrozenSet[str] = SUPPORTED_CORE_VERSIONS,
    ) -> None:
        assert TRANSPORT_RETRIES_ENABLED is False
        if (child_observer is not None
                and (child_alive_before is not None
                     or child_alive_after is not None)):
            raise ValueError(
                "Pass either child_observer or explicit "
                "child_alive_before/after, not both.")
        self._base_url = base_url or default_base_url()
        self._transport = transport or urllib_transport(self._base_url)
        self._clock = clock
        self._sleeper = sleeper
        self._api_contract = api_contract
        self._child_observer = child_observer
        # No default simulates confirmed liveness: explicit True required.
        self._child_alive_before = child_alive_before
        self._child_alive_after = child_alive_after
        self._installed_version_provider = installed_version_provider
        self._supported_versions = supported_versions

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_contract(self) -> VersionAdmission:
        return self._api_contract

    def _now(self) -> float:
        if self._clock is None:
            import time
            return time.monotonic()
        return float(self._clock.monotonic())

    def _sleep(self, delay: float) -> None:
        if delay <= 0:
            return
        if self._sleeper is None:
            import time
            time.sleep(delay)
            return
        self._sleeper(delay)

    def _observe_child(self, which: str) -> Optional[bool]:
        """Read-only liveness: True alive, False exited, None unknown."""
        if self._child_observer is not None:
            try:
                result = self._child_observer()
            except Exception:
                return None
            if result is True:
                return True
            if result is False:
                return False
            return None
        if which == "before":
            return self._child_alive_before
        return self._child_alive_after

    def _resolve_contract(self, health_body: Any) -> VersionAdmission:
        """Resolve DEC-A admission for one pass (fail-closed).

        When no installed-version provider is configured, the explicit
        ``api_contract`` input is preserved (existing contract/tests). When
        a provider is configured, DEC-A is evaluated from the installed
        version and the observed /health body; the static input is not
        consulted for that pass.
        """
        if self._installed_version_provider is None:
            return self._api_contract
        try:
            installed = self._installed_version_provider()
        except Exception:
            installed = None
        return evaluate_version_admission(
            installed, health_body,
            supported_versions=self._supported_versions)

    @staticmethod
    def _to_predicate(value: Optional[bool]) -> bool:
        # Only explicit True counts as observed-alive; False/None fail closed.
        return value is True

    def probe_once(
        self,
        *,
        now: Optional[float] = None,
        deadline: Optional[Deadline] = None,
    ) -> ReadinessProbeResult:
        """Probe the three endpoints exactly once each (one pass)."""
        current = self._now() if now is None else float(now)
        observations: Dict[str, ProbeObservation] = {}
        attempts: List[ProbeAttempt] = []
        failed: List[tuple] = []
        overrun = False
        health_body: Any = None
        alive_before = self._observe_child("before")
        for path in READINESS_PROBE_PATHS:
            if deadline is not None and not deadline.may_start_request(current):
                observations[path] = ProbeObservation(path=path, status=None)
                attempts.append(ProbeAttempt(path=path, timeout=0.0))
                failed.append((path, "deadline"))
                continue
            if deadline is not None:
                timeout = deadline.effective_request_timeout(current)
            else:
                timeout = MAX_REQUEST_TIMEOUT_SECONDS
            try:
                response = self._transport(path, timeout=timeout)
                status = response.status if response is not None else None
            except HttpRequestTimeout:
                attempts.append(ProbeAttempt(path=path, timeout=timeout, timed_out=True))
                observations[path] = ProbeObservation(path=path, status=None)
                failed.append((path, "timeout"))
                continue
            except Exception:
                attempts.append(ProbeAttempt(path=path, timeout=timeout,
                                             transport_error=True))
                observations[path] = ProbeObservation(path=path, status=None)
                failed.append((path, "transport"))
                continue
            # Post-request deadline check: wall-clock time ACTUALLY elapsed
            # is re-read from the SAME clock, so an overrun is detected
            # (not preempted). When the caller drives deterministic time
            # explicitly (now=...) with no clock injected, there is no
            # wall-clock to re-read, so the logical time is kept.
            if self._clock is not None or now is None:
                current = self._now()
            if deadline is not None and deadline.is_expired(current):
                overrun = True
            attempts.append(ProbeAttempt(path=path, timeout=timeout, status=status))
            observations[path] = ProbeObservation(path=path, status=status)
            if path == HEALTH_PATH:
                health_body = (
                    response.body if response is not None else None)
            if not is_http_success(status):
                failed.append((path, "status"))
        alive_after = self._observe_child("after")
        contract = self._resolve_contract(health_body)
        readiness = evaluate_readiness(
            ReadinessObservations(
                child_alive_before=self._to_predicate(alive_before),
                health=observations.get(HEALTH_PATH),
                models=observations.get(MODELS_PATH),
                artifacts=observations.get(ARTIFACTS_PATH),
                child_alive_after=self._to_predicate(alive_after),
                api_contract=contract,
            )
        )
        code = self._classify(readiness, failed, overrun=overrun)
        return ReadinessProbeResult(ready=readiness.ready, outcome=readiness,
                                    attempts=tuple(attempts), error_code=code)
    @staticmethod
    def _stage_code(path: str) -> ErrorCode:
        if path == HEALTH_PATH:
            return ErrorCode.HEALTH_CHECK_FAILED
        return ErrorCode.API_PROBE_FAILED

    def _classify(self, readiness: ReadinessOutcome, failed: List[tuple],
                  *, overrun: bool = False):
        if readiness.ready and not overrun:
            return None
        if overrun:
            # Detected after the fact: wall-clock exceeded the global budget
            # during a request. Never report success past the deadline.
            return ErrorCode.READINESS_TIMEOUT
        non_probe = [f for f in readiness.failures if f.condition in (
            ReadinessCondition.API_CONTRACT, ReadinessCondition.CHILD_ALIVE_BEFORE,
            ReadinessCondition.CHILD_ALIVE_AFTER)]
        if non_probe:
            return readiness.error_code()
        if len(failed) == 1:
            return self._stage_code(failed[0][0])
        return None

    def wait_until_ready(
        self,
        *,
        now: Optional[float] = None,
        deadline: Optional[Deadline] = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> ReadinessWaitResult:
        """Poll passes until ready or the global deadline expires (DEC-E)."""
        start = self._now() if now is None else float(now)
        active = deadline if deadline is not None else Deadline.starting_now(start)
        passes = 0
        last: Optional[ReadinessProbeResult] = None
        current = start
        while True:
            if active.is_expired(current):
                outcome = (last.outcome if last is not None
                           else evaluate_readiness(ReadinessObservations(
                               api_contract=self._api_contract)))
                return ReadinessWaitResult(ready=False, outcome=outcome, passes=passes,
                                           error_code=ErrorCode.READINESS_TIMEOUT,
                                           deadline_expired=True)
            result = self.probe_once(now=current, deadline=active)
            passes += 1
            last = result
            if result.ready:
                return ReadinessWaitResult(ready=True, outcome=result.outcome,
                                           passes=passes, error_code=None,
                                           deadline_expired=False)
            delay = active.next_poll_delay(current, poll_interval)
            current += delay
            self._sleep(delay)


__all__ = [
    "HttpProbeResponse",
    "HttpRequestTimeout",
    "HttpTransportError",
    "ProbeAttempt",
    "READINESS_PROBE_PATHS",
    "ReadinessHttpClient",
    "ReadinessProbeResult",
    "ReadinessWaitResult",
    "default_base_url",
    "urllib_transport",
]
