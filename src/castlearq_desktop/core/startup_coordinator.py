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

"""Bounded startup coordination: launch, observe, readiness (no GUI).

Wires the existing verified components with the smallest possible glue:
settings store (explicit path) → validator → SubprocessProcessAdapter +
LifecycleManager (single launch) → LifecycleObserver (100 ms) →
ReadinessHttpClient with liveness derived from observable manager state.

Liveness mapping (strict): RUNNING → True; UNEXPECTED_EXIT/STOPPED →
False; START_FAILED without owned handle → False; anything else → None
(unknown, fail-closed, no invented code). Read-only: never signals.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, FrozenSet, Optional



import json
import socket
from urllib.error import URLError

from castlearq_desktop.core.readiness_client import HttpRequestTimeout

from castlearq_desktop.core.contracts import Deadline
from castlearq_desktop.core.contracts import ErrorCode
from castlearq_desktop.core.contracts import VersionAdmission
from castlearq_desktop.core.readiness_client import urllib_transport  # noqa: F401
from castlearq_desktop.core.readiness_client import ProbeObservation  # noqa: F401
from castlearq_desktop.core.interpreter_settings import InterpreterSettingsStore
from castlearq_desktop.core.interpreter_validator import validate_python_interpreter
from castlearq_desktop.core.lifecycle_manager import LifecycleManager
from castlearq_desktop.core.lifecycle_manager import LifecycleState
from castlearq_desktop.core.lifecycle_observer import LifecycleObserver
from castlearq_desktop.core.readiness_client import HttpProbeResponse
from castlearq_desktop.core.readiness_client import ReadinessHttpClient
from castlearq_desktop.core.readiness_client import ReadinessWaitResult
from castlearq_desktop.core.subprocess_adapter import SubprocessProcessAdapter
from castlearq_desktop.core.version_admission import (
    extract_health_version,
    HEALTH_VERSION_FIELD,
    is_valid_version,
    classify_version_admission,
    SUPPORTED_CORE_VERSIONS,
)
from castlearq_desktop.core.version_admission import query_installed_core_version


def manager_liveness(manager: LifecycleManager) -> Optional[bool]:
    """Derive read-only liveness from observable manager state."""
    state = manager.state
    if state is LifecycleState.RUNNING:
        return True
    if state in (LifecycleState.UNEXPECTED_EXIT, LifecycleState.STOPPED):
        return False
    if state is LifecycleState.START_FAILED and not manager.has_owned_handle:
        return False
    return None


@dataclass
class CoreStartupCoordinator:
    """Minimal glue: validate → launch → observe → readiness."""

    settings: InterpreterSettingsStore
    clock: Any
    sleeper: Optional[Callable[[float], None]] = None
    transport: Optional[Callable[..., HttpProbeResponse]] = None
    timer_factory: Optional[Callable[[], Any]] = None
    validator: Optional[Callable[..., Any]] = None
    popen_factory: Optional[Callable[..., Any]] = None
    api_contract: VersionAdmission = VersionAdmission.NOT_EVALUATED
    installed_version_provider: Optional[Callable[[], Any]] = None
    version_executor: Optional[Callable[..., Any]] = None
    supported_versions: FrozenSet[str] = SUPPORTED_CORE_VERSIONS
    manager: Optional[LifecycleManager] = None
    qt_nam: Optional[Any] = None
    #: Optional worker pool used to run the installed-version probe off the
    #: GUI thread. ``None`` preserves the original bounded synchronous query,
    #: which is what the deterministic tests and the opt-in runtime suite
    #: exercise. Production injects a pool (see ``app.py``) so readiness never
    #: blocks the GUI thread on the 5 s subprocess.
    probe_pool: Optional[Any] = None
    observer: Optional[LifecycleObserver] = field(default=None, init=False)
    spawn_calls: int = field(default=0, init=False)
    _last_readiness_result: Optional[Any] = field(default=None, init=False)
    _last_readiness_error: Optional[Any] = field(default=None, init=False)
    # M1: installed-version resolution cache, at most one subprocess per
    # startup attempt. ``_version_cache_valid`` marks whether
    # ``_cached_installed_version`` belongs to the current attempt; every
    # accepted launch resets it so no value leaks across attempts.
    _cached_installed_version: Optional[str] = field(default=None, init=False)
    _version_cache_valid: bool = field(default=False, init=False)
    # M1 (async): identity of the startup attempt that owns the version
    # cache. Every accepted launch advances it, so a probe result delivered
    # for a superseded attempt can be recognised and dropped.
    _version_generation: int = field(default=0, init=False)
    # Whether the asynchronous probe has already been launched for the
    # current generation: exactly one probe per startup attempt.
    _version_probe_launched: bool = field(default=False, init=False)
    # Single Qt bridge for this coordinator's lifetime (see
    # ``core/version_probe.py``). Kept alive so a queued delivery always has
    # a live receiver.
    _probe_bridge: Optional[Any] = field(default=None, init=False)
    # Readiness transport of the active attempt, used to resume a held pass.
    _active_transport: Optional[Any] = field(default=None, init=False)

    def _ensure_manager(self) -> LifecycleManager:
        if self.manager is None:
            self.manager = LifecycleManager(
                adapter=_CountingAdapter(self, self.popen_factory),
                clock=self.clock)
        return self.manager

    def _ensure_observer(self) -> LifecycleObserver:
        assert self.manager is not None
        if self.observer is None:
            self.observer = LifecycleObserver(
                self.manager, timer_factory=self.timer_factory)
        return self.observer

    def _liveness(self) -> Optional[bool]:
        assert self.manager is not None
        return manager_liveness(self.manager)

    def launch(self) -> Any:
        """Validate config and perform the single launch (no recovery)."""
        from castlearq_desktop.core.contracts import CastleArqError
        from castlearq_desktop.core.lifecycle_manager import OperationResult
        python = self.settings.load()
        if not python:
            error = CastleArqError(ErrorCode.INTERPRETER_INVALID,
                                   "No interpreter configured.",
                                   {"configured": False})
            return OperationResult(status="failed",
                                   state=LifecycleState.NOT_STARTED,
                                   error=error, detail=str(error),
                                   context=dict(error.technical_context))
        outcome = (self.validator(python) if self.validator is not None
                   else validate_python_interpreter(python))
        if not outcome.is_valid:
            code = outcome.code or ErrorCode.INTERPRETER_INVALID
            error = CastleArqError(code, outcome.detail or "Invalid interpreter.",
                                   {"configured": True})
            return OperationResult(status="failed",
                                   state=LifecycleState.NOT_STARTED,
                                   error=error, detail=str(error),
                                   context=dict(error.technical_context))
        # M1: a new attempt starts here (validation passed, a launch outcome
        # — accepted, refused, or failed — is about to be produced). Reset
        # the version cache so the previous attempt's value never leaks, and
        # advance the generation so a probe still in flight for the previous
        # attempt is recognised as stale when it is delivered.
        self._version_cache_valid = False
        self._cached_installed_version = None
        self._version_generation += 1
        self._version_probe_launched = False
        manager = self._ensure_manager()
        result = manager.start(python)

        # Only start periodic observation when the launch requires tracking the
        # lifecycle. Definite validation/launch failures and suppressed launches
        # must not activate the timer unnecessarily.
        if result.state in (LifecycleState.STARTING, LifecycleState.RUNNING):
            self._ensure_observer().start()

        return result

    def _installed_version(self) -> Optional[str]:
        """Return the installed castlearq version for the current attempt.

        Three resolutions are possible, in order:

        1. an explicitly injected ``installed_version_provider`` resolves
           synchronously, exactly as before (tests and explicit injection);
        2. the production path launches the asynchronous probe once per
           attempt and reports the version as *pending* until the probe is
           delivered on the Qt thread;
        3. with no worker pool injected, the original bounded synchronous
           query is preserved unchanged.

        A probe that completed but failed resolves to ``None`` **and** marks
        the cache valid, so a resolution failure stays fail-closed and is
        never retried into a valid version within the same attempt.
        """
        if self._version_cache_valid:
            return self._cached_installed_version
        if self.installed_version_provider is not None:
            self._version_cache_valid = True
            try:
                value = self.installed_version_provider()
            except Exception:
                value = None
            self._cached_installed_version = (
                value if isinstance(value, str) else None)
            return self._cached_installed_version
        if self.probe_pool is not None:
            # Asynchronous production path: launch at most one probe for this
            # attempt and report the version as pending until it is delivered.
            self._ensure_version_probe()
            return self._cached_installed_version
        # No worker pool injected: preserve the original synchronous query.
        self._version_cache_valid = True
        return self._resolve_installed_version_synchronously()

    def _resolve_installed_version_synchronously(self) -> Optional[str]:
        """Resolve the installed version with the bounded synchronous query."""
        try:
            python = self.settings.load()
        except Exception:
            self._cached_installed_version = None
            return None
        if not python:
            self._cached_installed_version = None
            return None
        self._cached_installed_version = query_installed_core_version(
            python, execute=self.version_executor)
        return self._cached_installed_version

    def _ensure_version_probe(self) -> None:
        """Launch the asynchronous installed-version probe once per attempt.

        Never blocks: it only reads the configured interpreter and queues a
        worker. When no interpreter is configured the resolution is recorded
        immediately as absent, so readiness is fail-closed instead of being
        held forever with nothing in flight.
        """
        if self._version_probe_launched:
            return
        self._version_probe_launched = True
        if self.probe_pool is None:
            return
        try:
            python = self.settings.load()
        except Exception:
            python = None
        if not python:
            self._cached_installed_version = None
            self._version_cache_valid = True
            return
        self._launch_version_probe(python)

    def _launch_version_probe(self, python: str) -> None:
        """Queue one probe worker for the configured interpreter."""
        from castlearq_desktop.core.version_probe import (
            VersionProbeBridge,
            VersionProbeRunnable,
        )

        bridge = self._ensure_probe_bridge()
        runnable = VersionProbeRunnable(
            weakref.ref(bridge),
            self._version_generation,
            python,
            executor=self.version_executor,
        )
        self.probe_pool.start(runnable)

    def _ensure_probe_bridge(self) -> Any:
        """Return this coordinator's single Qt bridge, creating it lazily.

        One bridge for the coordinator's lifetime: it is created on the
        thread that runs readiness (the GUI thread in production), so every
        delivery is queued back to that thread.
        """
        if self._probe_bridge is None:
            from castlearq_desktop.core.version_probe import VersionProbeBridge

            self._probe_bridge = VersionProbeBridge(self._on_version_delivered)
        return self._probe_bridge

    def _version_pending(self) -> bool:
        """Whether the production version probe is still in flight.

        Consulted by the readiness transport to decide whether a pass may be
        evaluated yet. Injected providers and the synchronous fallback always
        resolve immediately, so they are never pending.
        """
        if self.installed_version_provider is not None:
            return False
        if self.probe_pool is None:
            return False
        if self._version_cache_valid:
            return False
        self._ensure_version_probe()
        return not self._version_cache_valid

    def _on_version_delivered(self, payload: Any) -> None:
        """Receive a delivered probe result on the Qt (GUI) thread.

        Only this method writes the version cache. A result whose generation
        no longer matches the active attempt is discarded, so a superseded
        attempt can never overwrite the current one.
        """
        generation, version = payload
        if generation != self._version_generation:
            return  # stale: a newer attempt owns the cache now
        self._cached_installed_version = (
            version if isinstance(version, str) else None)
        self._version_cache_valid = True
        transport = self._active_transport
        if transport is not None:
            try:
                transport.resume()
            except Exception:  # noqa: BLE001 - a finished transport is inert
                pass

    def invalidate_version_probe(self) -> None:
        """Retire the active probe generation (logical invalidation only).

        Does not cancel the underlying subprocess: an in-flight probe keeps
        running to completion, but its result is discarded because the
        generation no longer matches. Never blocks the caller.
        """
        self._version_generation += 1
        self._version_probe_launched = True
        self._active_transport = None

    def check_readiness_once(
        self,
        *,
        deadline: Optional[Deadline] = None,
        now: Optional[float] = None,
    ) -> Any:
        """Single readiness pass (deterministic; no polling loop)."""
        manager = self._ensure_manager()
        assert manager is not None
        client = ReadinessHttpClient(
            transport=self.transport, clock=self.clock, sleeper=self.sleeper,
            api_contract=self.api_contract, child_observer=self._liveness,
            installed_version_provider=self._installed_version,
            supported_versions=self.supported_versions)
        start = self.clock.monotonic() if now is None else float(now)
        active = deadline if deadline is not None else Deadline.starting_now(start)
        return client.probe_once(now=start, deadline=active)

    def check_readiness(
        self,
        *,
        deadline: Optional[Deadline] = None,
        now: Optional[float] = None,
    ) -> ReadinessWaitResult:
        """Run HTTP readiness with manager-derived liveness (fail-closed)."""
        manager = self._ensure_manager()
        assert manager is not None
        client = ReadinessHttpClient(
            transport=self.transport, clock=self.clock, sleeper=self.sleeper,
            api_contract=self.api_contract, child_observer=self._liveness,
            installed_version_provider=self._installed_version,
            supported_versions=self.supported_versions)
        start = self.clock.monotonic() if now is None else float(now)
        active = deadline if deadline is not None else Deadline.starting_now(start)
        return client.wait_until_ready(now=start, deadline=active)

    def check_readiness_async(
        self,
        *,
        deadline: Optional[Deadline] = None,
        now: Optional[float] = None,
    ) -> "QtReadinessChecker":
        """Run readiness asynchronously over the Qt event loop (DEC-B / DEC-E).

        The call returns immediately and delivers the final
        :class:`ReadinessWaitResult` through the checker's ``result`` signal.
        The Qt event loop drives the entire readiness flow: each HTTP request is
        a non-blocking ``QNetworkAccessManager`` GET, and ``QTimer`` drives the
        per-request timeout (10 s, capped by the global 20 s deadline), the
        250 ms polling interval, and the global deadline.

        The checker is lifecycle-neutral: it cancels only the network requests
        and Qt timers. It never touches the process, the lifecycle manager, or
        the shutdown policy.

        Returns the :class:`QtReadinessChecker` so the caller can ``stop()`` it
        early, connect to ``result``/``error``, or dispose of it.
        """
        from PySide6.QtCore import QTimer

        from castlearq_desktop.core.readiness_client import default_base_url
        from castlearq_desktop.core.readiness_qt_transport import (
            QtReadinessChecker,
            QtReadinessTransport,
        )

        manager = self._ensure_manager()
        assert manager is not None
        start = self.clock.monotonic() if now is None else float(now)
        active = deadline if deadline is not None else Deadline.starting_now(start)
        timer_factory = self.timer_factory if self.timer_factory is not None else QTimer
        transport = QtReadinessTransport(
            default_base_url(),
            child_observer=self._liveness,
            api_contract=self.api_contract,
            clock=self.clock,
            timer_factory=timer_factory,
            nam=self.qt_nam,
            installed_version_provider=self._installed_version,
            version_pending=self._version_pending,
            supported_versions=self.supported_versions,
        )
        # Retained so a delivered version can resume a held pass. Replaced by
        # the next attempt; a finished transport makes resume() inert.
        self._active_transport = transport
        checker = QtReadinessChecker(transport)
        checker.result.connect(self._on_readiness_result)
        checker.error.connect(self._on_readiness_error)
        checker.start(now=start, deadline=active)
        return checker

# Part 1: signature, docstring, imports, base_url, transport, probes.
# This file is a fragment; do not run it directly.

    def connect_existing(
        self,
        *,
        now: Optional[float] = None,
        deadline: Optional[Deadline] = None,
        base_url: Optional[str] = None,
        installed_version_provider: Optional[Callable[[], Optional[str]]] = None,
    ) -> ConnectionAdmission:
        """Check if a compatible Core instance is already running (Desktop).

        Probes the Core HTTP endpoints (/health, /v1/models, /v1/artifacts) using
        the configured transport without launching any process.

        Version admission uses the central DEC-A classifier over the remote Core
        version observed in GET /health and Desktop's installed version (supplied
        by *installed_version_provider* when configured). Fail-closed: a version
        that is format-valid but not admitted by the central allowlist is reported
        as incompatible; when the installed version is unavailable without
        launching a subprocess the admission is reported as *not evaluated*.

        Returns a ConnectionAdmission. *status* carries the outcome; *code* reuses
        existing ErrorCode values where an approved code exists; *version_admission*
        carries the DEC-A classification; *version* is the version observed in
        GET /health when present.
        """
        from castlearq_desktop.core.contracts.readiness import (
            HEALTH_PATH,
            MODELS_PATH,
            ARTIFACTS_PATH,
            is_http_success,
        )
        from urllib.error import URLError

        # Use provided base URL or default from the port-conflict config.
        if base_url is None:
            base_url = self._get_base_url()
        if base_url is None:
            return ConnectionAdmission(
                status=ConnectionAdmissionStatus.NOT_CONFIGURED,
                code=None,
                version_admission=VersionAdmission.NOT_EVALUATED,
                version=None,
                detail="No Core base URL configured and none supplied.",
                context={"base_url": base_url},
            )

        # Build the transport (test injection or the standard urllib transport).
        transport = self.transport if self.transport is not None else urllib_transport(base_url)

        # Probe the models endpoint first.
        raw_models = None
        try:
            raw_models = transport(MODELS_PATH, timeout=5.0)
        except Exception as exc:  # noqa: BLE001 - probe-local classification
            status = self._classify_probe_error(exc)
            if status is ConnectionAdmissionStatus.TIMEOUT:
                return ConnectionAdmission(
                    status=ConnectionAdmissionStatus.TIMEOUT,
                    code=None,
                    version_admission=VersionAdmission.NOT_EVALUATED,
                    version=None,
                    detail="GET /v1/models timed out.",
                    context={"path": MODELS_PATH},
                )
            return ConnectionAdmission(
                status=ConnectionAdmissionStatus.CONNECTION_REFUSED,
                code=None,
                version_admission=VersionAdmission.NOT_EVALUATED,
                version=None,
                detail="GET /v1/models did not connect.",
                context={"path": MODELS_PATH, "error_type": type(exc).__name__},
            )

        # Probe the health endpoint.
        raw_health = None
        try:
            raw_health = transport(HEALTH_PATH, timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            status = self._classify_probe_error(exc)
            if status is ConnectionAdmissionStatus.TIMEOUT:
                return ConnectionAdmission(
                    status=ConnectionAdmissionStatus.TIMEOUT,
                    code=None,
                    version_admission=VersionAdmission.NOT_EVALUATED,
                    version=None,
                    detail="GET /health timed out.",
                    context={"path": HEALTH_PATH},
                )
            return ConnectionAdmission(
                status=ConnectionAdmissionStatus.CONNECTION_REFUSED,
                code=None,
                version_admission=VersionAdmission.NOT_EVALUATED,
                version=None,
                detail="GET /health did not connect.",
                context={"path": HEALTH_PATH, "error_type": type(exc).__name__},
            )

        # Required endpoint failed -> not an admitted connection.
        models_status = getattr(raw_models, "status", None) if raw_models is not None else None
        if not is_http_success(models_status):
            return ConnectionAdmission(
                status=ConnectionAdmissionStatus.HTTP_ERROR,
                code=ErrorCode.API_PROBE_FAILED,
                version_admission=VersionAdmission.NOT_EVALUATED,
                version=None,
                detail="GET /v1/models did not return a 2xx status.",
                context={"path": MODELS_PATH, "status": models_status},
            )

        # If health fails, report HEALTH_CHECK_FAILED.
        health_status = getattr(raw_health, "status", None) if raw_health is not None else None
        if not is_http_success(health_status):
            return ConnectionAdmission(
                status=ConnectionAdmissionStatus.HTTP_ERROR,
                code=ErrorCode.HEALTH_CHECK_FAILED,
                version_admission=VersionAdmission.NOT_EVALUATED,
                version=None,
                detail="GET /health did not return a 2xx status.",
                context={"path": HEALTH_PATH, "status": health_status},
            )

        # Version admission (DEC-A): present but malformed is incompatible;
        # present and valid is classified against the central allowlist.
        body = raw_health.body
        version_admission = VersionAdmission.NOT_EVALUATED
        version: Optional[str] = None
        if body is not None:
            # A non-JSON body is an invalid response, not a missing version.
            try:
                json.loads(body)
            except (ValueError, TypeError):
                return ConnectionAdmission(
                    status=ConnectionAdmissionStatus.INVALID_RESPONSE,
                    code=None,
                    version_admission=VersionAdmission.MALFORMED,
                    version=None,
                    detail="GET /health body is not valid JSON.",
                    context={"path": HEALTH_PATH},
                )
            present, health_version = extract_health_version(body)
            if not present:
                return ConnectionAdmission(
                    status=ConnectionAdmissionStatus.MISSING_VERSION,
                    code=None,
                    version_admission=VersionAdmission.MISSING,
                    version=None,
                    detail="GET /health returned no version field.",
                    context={"path": HEALTH_PATH},
                )
            if not is_valid_version(health_version):
                return ConnectionAdmission(
                    status=ConnectionAdmissionStatus.MALFORMED_VERSION,
                    code=ErrorCode.API_CONTRACT_INCOMPATIBLE,
                    version_admission=VersionAdmission.MALFORMED,
                    version=health_version,
                    detail="GET /health version is not a valid version string.",
                    context={"version": health_version, "path": HEALTH_PATH},
                )
            version = health_version
            # Resolved provider: a call-time override takes precedence over the
            # configured installed-version provider. Either way, a missing
            # provider means admission cannot be concluded without spawning a
            # subprocess; no subprocess is launched here.
            provider = installed_version_provider if installed_version_provider is not None else self.installed_version_provider
            if provider is None:
                version_admission = VersionAdmission.NOT_EVALUATED
            else:
                installed = provider()
                if installed is None:
                    version_admission = VersionAdmission.NOT_EVALUATED
                else:
                    version_admission = classify_version_admission(
                        installed,
                        version,
                        supported_versions=self.supported_versions,
                    )

        # Artifacts is optional at this desktop-level probe; a failure never
        # drives the outcome, it is informational only.
        try:
            transport(ARTIFACTS_PATH, timeout=5.0)
        except Exception:  # noqa: BLE001
            pass

        if version_admission is VersionAdmission.ADMITTED:
            return ConnectionAdmission(
                status=ConnectionAdmissionStatus.ADMITTED,
                code=None,
                version_admission=VersionAdmission.ADMITTED,
                version=version,
                detail="Core connection admitted and ready.",
                context={"version": version, "path": HEALTH_PATH},
            )

        # Version valid but not admitted: match against the central allowlist.
        if version_admission in (
            VersionAdmission.INCOMPATIBLE,
            VersionAdmission.UNKNOWN,
        ):
            return ConnectionAdmission(
                status=ConnectionAdmissionStatus.INCOMPATIBLE_VERSION,
                code=ErrorCode.API_CONTRACT_INCOMPATIBLE,
                version_admission=version_admission,
                version=version,
                detail="Core version is format-valid but not admitted by DEC-A.",
                context={"version": version, "admission": version_admission.value},
            )

        # Remaining version outcomes: unclassified or not concluded.
        detail = {
            VersionAdmission.MISSING: "GET /health returned no version field.",
            VersionAdmission.MALFORMED: "GET /health version is not a valid version string.",
            VersionAdmission.NOT_EVALUATED: "Installed version unavailable; admission not concluded.",
            VersionAdmission.UNKNOWN: "Core version is format-valid but not admitted by DEC-A.",
        }.get(version_admission, "Core version admission not concluded.")

        return ConnectionAdmission(
            status=ConnectionAdmissionStatus.INCOMPATIBLE_VERSION
            if version_admission in (
                VersionAdmission.INCOMPATIBLE,
                VersionAdmission.UNKNOWN,
            )
            else ConnectionAdmissionStatus.NOT_EVALUATED,
            code=ErrorCode.API_CONTRACT_INCOMPATIBLE
            if version_admission in (
                VersionAdmission.INCOMPATIBLE,
                VersionAdmission.UNKNOWN,
            )
            else None,
            version_admission=version_admission,
            version=version,
            detail=detail,
            context={"version": version, "admission": version_admission.value},
        )

    @staticmethod
    def _classify_probe_error(exc: BaseException) -> ConnectionAdmissionStatus:
        """Classify a transport-level probe failure without launching a subprocess.

        Uses the platform's socket timeout exception so that a plain timeout is
        reported as :attr:`ConnectionAdmissionStatus.TIMEOUT` instead of a
        refused connection.
        """
        if isinstance(exc, HttpRequestTimeout):
            return ConnectionAdmissionStatus.TIMEOUT
        if isinstance(exc, URLError) and isinstance(
            exc.reason, (socket.timeout, TimeoutError)
        ):
            return ConnectionAdmissionStatus.TIMEOUT
        return ConnectionAdmissionStatus.CONNECTION_REFUSED

    def _get_base_url(self) -> Optional[str]:
        """Get the base URL for Core connection check."""
        from castlearq_desktop.core.contracts.port_conflict import CORE_HOST, CORE_PORT
        return f"http://{CORE_HOST}:{CORE_PORT}"

    def _on_readiness_result(self, result: ReadinessWaitResult) -> None:
        self._last_readiness_result = result
        self._active_transport = None

    def _on_readiness_error(self, exc: Exception) -> None:
        self._last_readiness_error = exc
        self._active_transport = None
class _CountingAdapter(SubprocessProcessAdapter):
    """Adapter that counts spawns (no behaviour change)."""

    def __init__(self, owner: CoreStartupCoordinator,
                 popen_factory: Optional[Callable[..., Any]] = None) -> None:
        super().__init__(popen_factory=popen_factory)
        self._owner = owner

    def spawn(self, argv, *, shell):
        self._owner.spawn_calls += 1
        return super().spawn(argv, shell=shell)



class ConnectionAdmissionStatus(Enum):
    """Outcome of a Desktop-level connection admission (connect_existing).

    Desktop-level: this is not a Core readiness contract. ErrorCode members are
    reused from :mod:`contracts.errors` where an approved code exists; otherwise
    the outcome is reported as an unclassified status.
    """

    ADMITTED = "admitted"
    CONNECTION_REFUSED = "connection_refused"
    NOT_CONFIGURED = "not_configured"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"
    MALFORMED_VERSION = "malformed_version"
    INCOMPATIBLE_VERSION = "incompatible_version"
    MISSING_VERSION = "missing_version"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class ConnectionAdmission:
    """Outcome of a Desktop-level connection admission.

    ``status`` carries the outcome; ``code`` reuses existing ErrorCode values
    where an approved code exists; ``version_admission`` carries the DEC-A
    classification; ``version`` is the version observed in GET /health
    (if any); ``context`` carries structured diagnostics.
    """

    status: ConnectionAdmissionStatus
    code: Optional[ErrorCode]
    version_admission: VersionAdmission
    version: Optional[str]
    detail: str
    context: Dict[str, Any] = field(default_factory=dict)


__all__ = ["CoreStartupCoordinator", "manager_liveness"]
