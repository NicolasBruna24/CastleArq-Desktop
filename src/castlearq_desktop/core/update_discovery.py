# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0 (the "License").

"""Core Update Discovery v1 — manual PyPI update-discovery service (Stage 1).

Implements the ratified specification ``docs/core-update-discovery-spec.md``
(DEC-UD-1..4): on an explicit trigger, ask the official PyPI JSON endpoint for
the latest *stable, installable, not-withdrawn* ``castlearq`` release and
relate it to (a) the really-installed version and (b) Desktop's local DEC-A
admission allowlist.

Scope and posture (spec §3, §13 Stage 1):

* **Informational only.** Discovery never admits a version, never installs or
  downloads anything, and shares no state with launch/readiness/shutdown.
  ``SUPPORTED_CORE_VERSIONS`` is read (as a snapshot) and never written.
* **Synchronous, injectable service.** This module is the core logic with
  injectable transport/executor/clock seams (same style as
  ``readiness_client``). The Qt asynchronous wrapper and the
  Settings → Updates UI belong to Stage 3 and are not implemented here;
  dropping late/superseded results is likewise a UI-layer duty (Stage 3).
* **Fixed destination, no retries.** Exactly one HTTPS GET to
  ``https://pypi.org/pypi/castlearq/json``; redirects that leave
  ``pypi.org`` are refused at the handler level; a single attempt per call.
* **Absolute deadline (DEC-UD-2).** ``MAX_UPDATE_CHECK_TIMEOUT_SECONDS``
  (10 s) is computed once per call and enforced before the request, before
  every read chunk, and after the read — it never restarts. Honest temporal
  limitation (documented identically for the readiness ``urllib``
  transport): a single blocked ``recv`` cannot be preempted exactly at the
  deadline; it is bounded by the socket timeout set at open time (the
  remaining budget then), so the total is bounded by the deadline plus at
  most one socket timeout.
* **Bounded body (DEC-UD-2).** At most ``MAX_UPDATE_RESPONSE_BYTES`` (2 MiB)
  is buffered; a declared oversized ``Content-Length`` is rejected *before*
  any body byte is accumulated.
* **PEP 440 via ``packaging`` (DEC-UD-1).** All parsing, ordering, and
  pre-release detection is delegated to ``packaging.version``; this module
  implements no comparison logic of its own and invalid version strings are
  treated fail-closed (never comparable, never admitted).
* **Local errors (DEC-UD-3).** Failures use the discovery-local
  ``UpdateDiscoveryError`` with the stable ratified reasons; the global
  DEC-D ``ErrorCode`` catalog is not touched.

The minimum supported ``packaging`` (``>=26.3`` in ``pyproject.toml``) was
chosen by the ratified §6.2 rule: the newest stable ``packaging`` release
verified at implementation time in the project environment, whose declared
``Requires-Python`` (``>=3.9``) covers Desktop's floor (Python 3.10+), with
the required API contract-checked in isolation (``Version`` ordering,
``is_prerelease``, ``InvalidVersion`` raised on invalid input).
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional, Tuple
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from packaging.version import InvalidVersion
from packaging.version import Version

from castlearq_desktop.core.version_admission import SUPPORTED_CORE_VERSIONS
from castlearq_desktop.core.version_admission import query_installed_core_version

#: The single fixed official endpoint (spec §8.1). Never configurable.
PYPI_PROJECT_JSON_URL = "https://pypi.org/pypi/castlearq/json"

#: The only host any request or redirect may ever touch (spec §8.1).
PYPI_HOST = "pypi.org"

#: Absolute total deadline per discovery call, seconds (DEC-UD-2).
MAX_UPDATE_CHECK_TIMEOUT_SECONDS = 10.0

#: Maximum response body buffered for one call, bytes (DEC-UD-2: 2 MiB).
MAX_UPDATE_RESPONSE_BYTES = 2 * 1024 * 1024

#: Fixed descriptive user agent; no other custom headers are sent.
UPDATE_USER_AGENT = "CastleArq-Desktop/update-discovery-v1"

#: Bounded read granularity for the streaming size-cap enforcement.
_READ_CHUNK_BYTES = 64 * 1024

#: Cap on per-key diagnostic lists kept in technical context.
_MAX_CONTEXT_ITEMS = 20

#: Markers that must never appear in a user-facing message (spec §9).
_TRACEBACK_MARKERS = ("Traceback (most recent call last)", 'File "')


class UpdateDiscoveryFailureReason(str, Enum):
    """Stable, ratified failure reasons for discovery (spec §9, DEC-UD-3).

    Discovery-local only: these never map into the global DEC-D
    ``ErrorCode`` catalog, which stays unchanged.
    """

    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    HTTP_NON_SUCCESS = "HTTP_NON_SUCCESS"
    INVALID_JSON = "INVALID_JSON"
    INCOMPLETE_METADATA = "INCOMPLETE_METADATA"
    NO_STABLE_VERSION = "NO_STABLE_VERSION"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    INSTALLED_UNKNOWN = "INSTALLED_UNKNOWN"


_DEFAULT_USER_MESSAGES: Dict[UpdateDiscoveryFailureReason, str] = {
    UpdateDiscoveryFailureReason.NETWORK_ERROR:
        "The PyPI service could not be reached. Check the network connection "
        "and try again; your installed Core is unaffected.",
    UpdateDiscoveryFailureReason.TIMEOUT:
        "The update check exceeded its time limit and was abandoned. Nothing "
        "is implied about published versions.",
    UpdateDiscoveryFailureReason.HTTP_NON_SUCCESS:
        "The PyPI index answered with an unexpected status. Try again later.",
    UpdateDiscoveryFailureReason.INVALID_JSON:
        "The PyPI index response could not be understood.",
    UpdateDiscoveryFailureReason.INCOMPLETE_METADATA:
        "The PyPI index response is missing required metadata.",
    UpdateDiscoveryFailureReason.NO_STABLE_VERSION:
        "No valid stable release of castlearq could be identified.",
    UpdateDiscoveryFailureReason.RESPONSE_TOO_LARGE:
        "The PyPI index response exceeded the 2 MiB limit and was rejected.",
    UpdateDiscoveryFailureReason.INSTALLED_UNKNOWN:
        "The installed castlearq version could not be determined for the "
        "selected interpreter.",
}


def _reject_traceback_message(message: str) -> str:
    """Refuse a user-facing message that looks like a stack trace (spec §9)."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("A user-facing message must be a non-empty string.")
    for marker in _TRACEBACK_MARKERS:
        if marker in message:
            raise ValueError(
                "A stack trace must not be used as the primary user-facing "
                "message; put the detail in the technical context instead.")
    return message


class UpdateDiscoveryError(Exception):
    """Typed discovery-local error: stable reason + message + context (DEC-UD-3).

    ``str(error)`` is always the actionable user-facing message. Diagnostic
    detail lives exclusively in :attr:`technical_context` and
    :meth:`diagnostics`, which never contain a formatted traceback or a raw
    response body — the ``CastleArqError`` conventions, kept local so the
    global ``ErrorCode`` vocabulary is untouched.
    """

    def __init__(
        self,
        reason: UpdateDiscoveryFailureReason,
        user_message: Optional[str] = None,
        technical_context: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if not isinstance(reason, UpdateDiscoveryFailureReason):
            raise ValueError(f"Unapproved discovery reason: {reason!r}")
        self.reason = reason
        self.user_message = _reject_traceback_message(
            user_message if user_message is not None
            else _DEFAULT_USER_MESSAGES[reason])
        context: Dict[str, Any] = dict(technical_context or {})
        for key, value in context.items():
            if isinstance(value, str) and any(m in value for m in _TRACEBACK_MARKERS):
                raise ValueError(
                    "Technical context must not embed a stack trace; record "
                    f"structured facts instead (offending key: {key!r}).")
        self.technical_context: Mapping[str, Any] = MappingProxyType(context)
        super().__init__(self.user_message)

    def diagnostics(self) -> Dict[str, Any]:
        """Return a structured, traceback-free diagnostic payload."""
        payload: Dict[str, Any] = {
            "reason": self.reason.value,
            "technical_context": dict(self.technical_context),
        }
        cause = self.__cause__
        if cause is not None:
            payload["cause_type"] = type(cause).__name__
            payload["cause_message"] = str(cause)[:200]
        return payload

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"{type(self).__name__}(reason={self.reason.value!r}, "
                f"user_message={self.user_message!r})")


class CoreUpdateDiscoveryState(str, Enum):
    """Terminal discovery states plus the UI-only in-progress marker (spec §7).

    This synchronous service only ever *returns* the terminal states;
    ``IN_PROGRESS`` exists so the Stage-3 UI layer shares one vocabulary.
    """

    IN_PROGRESS = "in_progress"
    LATEST_INSTALLED = "latest_installed"
    NEWER_STABLE_ADMITTED = "newer_stable_admitted"
    NEWER_STABLE_NOT_ADMITTED = "newer_stable_not_admitted"
    INSTALLED_AHEAD = "installed_ahead"
    INSTALLED_UNKNOWN = "installed_unknown"
    SEARCH_FAILED = "search_failed"


#: The terminal states a discovery call can return (spec §7).
CORE_UPDATE_DISCOVERY_STATES: FrozenSet[CoreUpdateDiscoveryState] = frozenset({
    CoreUpdateDiscoveryState.LATEST_INSTALLED,
    CoreUpdateDiscoveryState.NEWER_STABLE_ADMITTED,
    CoreUpdateDiscoveryState.NEWER_STABLE_NOT_ADMITTED,
    CoreUpdateDiscoveryState.INSTALLED_AHEAD,
    CoreUpdateDiscoveryState.INSTALLED_UNKNOWN,
    CoreUpdateDiscoveryState.SEARCH_FAILED,
})


@dataclass(frozen=True)
class UpdateDiscoveryResult:
    """Outcome of one discovery call, keeping the three versions separate (spec §4).

    ``installed_failure`` (reason ``INSTALLED_UNKNOWN``) can coexist with a
    successful remote result, and a remote failure never masks it — both
    facts are carried independently (spec §9).
    """

    state: CoreUpdateDiscoveryState
    installed_version: Optional[str]
    latest_stable: Optional[str]
    admitted_versions: FrozenSet[str]
    search_failure: Optional[UpdateDiscoveryError] = None
    installed_failure: Optional[UpdateDiscoveryError] = None
    technical_context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "technical_context",
            MappingProxyType(dict(self.technical_context)))


class _PinnedHostRedirectHandler(urllib_request.HTTPRedirectHandler):
    """Refuse any redirect that leaves ``https://pypi.org`` (spec §8.1).

    Raising here (instead of validating after the fact) prevents the body of
    an off-host redirect target from ever being requested.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802 (urllib API)
        parsed = urllib_parse.urlparse(newurl)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host != PYPI_HOST:
            raise urllib_error.URLError(
                f"off-host redirect refused: scheme={parsed.scheme!r} "
                f"host={host!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_pypi_project_body(
    *,
    deadline: float,
    now: Callable[[], float] = time.monotonic,
    max_bytes: int = MAX_UPDATE_RESPONSE_BYTES,
    opener: Optional[Any] = None,
) -> bytes:
    """Fetch the fixed PyPI project JSON body under an absolute *deadline*.

    One attempt, no retries (spec §8.1). *deadline* is an absolute value on
    the *now* clock, computed once by the caller from the user trigger; it is
    checked before the request, before every read chunk, and after the read —
    it never restarts. The declared ``Content-Length`` (when present) is
    checked against *max_bytes* before any body byte is accumulated, and the
    streaming read stops as soon as the cap would be exceeded.

    Raises :class:`UpdateDiscoveryError` with the ratified reason for every
    failure (``TIMEOUT``, ``NETWORK_ERROR``, ``HTTP_NON_SUCCESS``,
    ``RESPONSE_TOO_LARGE``).
    """
    def _remaining() -> float:
        return deadline - now()

    remaining = _remaining()
    if remaining <= 0:
        raise UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.TIMEOUT,
            technical_context={"stage": "pre_request"})
    if opener is None:
        opener = urllib_request.build_opener(_PinnedHostRedirectHandler())
    request = urllib_request.Request(
        PYPI_PROJECT_JSON_URL,
        headers={"User-Agent": UPDATE_USER_AGENT},
        method="GET")
    try:
        response = opener.open(request, timeout=max(0.001, remaining))
    except urllib_error.HTTPError as exc:
        raise UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.HTTP_NON_SUCCESS,
            technical_context={"http_status": int(exc.code),
                               "url_host": PYPI_HOST}) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.TIMEOUT,
            technical_context={"stage": "open"}) from exc
    except urllib_error.URLError as exc:
        reason = (UpdateDiscoveryFailureReason.TIMEOUT
                  if isinstance(exc.reason, (TimeoutError, socket.timeout))
                  else UpdateDiscoveryFailureReason.NETWORK_ERROR)
        raise UpdateDiscoveryError(
            reason, technical_context={"stage": "open"}) from exc
    except OSError as exc:
        raise UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.NETWORK_ERROR,
            technical_context={"stage": "open"}) from exc

    with response:
        # Defense in depth: the redirect handler already pins the host.
        final = urllib_parse.urlparse(response.geturl())
        if (final.scheme != "https"
                or (final.hostname or "").lower() != PYPI_HOST):
            raise UpdateDiscoveryError(
                UpdateDiscoveryFailureReason.NETWORK_ERROR,
                technical_context={"stage": "final_url_check",
                                   "final_scheme": final.scheme,
                                   "final_host": (final.hostname or "").lower()})
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > max_bytes:
                    raise UpdateDiscoveryError(
                        UpdateDiscoveryFailureReason.RESPONSE_TOO_LARGE,
                        technical_context={"stage": "headers",
                                           "declared_bytes": int(declared),
                                           "max_bytes": max_bytes})
            except (TypeError, ValueError):
                pass  # Untrusted/unparseable header: rely on the stream cap.
        chunks: List[bytes] = []
        total = 0
        while True:
            if _remaining() <= 0:
                raise UpdateDiscoveryError(
                    UpdateDiscoveryFailureReason.TIMEOUT,
                    technical_context={"stage": "read",
                                       "bytes_read": total})
            try:
                chunk = response.read(
                    min(_READ_CHUNK_BYTES, max_bytes + 1 - total))
            except (TimeoutError, socket.timeout) as exc:
                raise UpdateDiscoveryError(
                    UpdateDiscoveryFailureReason.TIMEOUT,
                    technical_context={"stage": "read",
                                       "bytes_read": total}) from exc
            except OSError as exc:
                raise UpdateDiscoveryError(
                    UpdateDiscoveryFailureReason.NETWORK_ERROR,
                    technical_context={"stage": "read",
                                       "bytes_read": total}) from exc
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise UpdateDiscoveryError(
                    UpdateDiscoveryFailureReason.RESPONSE_TOO_LARGE,
                    technical_context={"stage": "stream",
                                       "bytes_read": total,
                                       "max_bytes": max_bytes})
            chunks.append(chunk)
    if _remaining() <= 0:
        raise UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.TIMEOUT,
            technical_context={"stage": "post_read",
                               "bytes_read": total})
    return b"".join(chunks)


def select_latest_stable_version(body: bytes) -> Tuple[str, Dict[str, Any]]:
    """Validate a PyPI project payload and return ``(latest_key, context)``.

    The selection policy of spec §6.2/§8.2, delegated to ``packaging``:

    * every ``releases`` key is parsed with ``Version``; unparseable keys are
      discarded as invalid metadata (never compared, never admitted);
    * pre-releases are excluded (v1 has no pre-release channel);
    * entries with no usable file — an empty list, a wrong-typed value, or
      files all flagged ``yanked``/withdrawn — are excluded, so "latest
      stable" means *installable and not withdrawn*;
    * the maximum of the remainder under PEP 440 ordering wins; the original
      key string is returned;
    * ``info.version`` is only a cross-check and never authoritative.

    Raises :class:`UpdateDiscoveryError` (``INVALID_JSON``,
    ``INCOMPLETE_METADATA``, ``NO_STABLE_VERSION``) on unusable payloads.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.INVALID_JSON,
            technical_context={"body_bytes": len(body)}) from exc
    if not isinstance(payload, dict):
        raise UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.INCOMPLETE_METADATA,
            technical_context={"top_level_type": type(payload).__name__})
    releases = payload.get("releases")
    if not isinstance(releases, dict):
        raise UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.INCOMPLETE_METADATA,
            technical_context={
                "releases_type": type(releases).__name__})

    candidates: List[Tuple[Version, str]] = []
    invalid_keys: List[str] = []
    excluded_prereleases = 0
    excluded_unusable = 0
    for raw_key, files in releases.items():
        key = str(raw_key)
        try:
            version = Version(key)
        except InvalidVersion:
            invalid_keys.append(key[:64])
            continue
        if version.is_prerelease:
            excluded_prereleases += 1
            continue
        usable = [f for f in files
                  if isinstance(f, dict) and not f.get("yanked")] \
            if isinstance(files, list) else []
        if not usable:
            excluded_unusable += 1
            continue
        candidates.append((version, key))
    if not candidates:
        raise UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.NO_STABLE_VERSION,
            technical_context={
                "release_keys": len(releases),
                "invalid_version_keys": invalid_keys[:_MAX_CONTEXT_ITEMS],
                "excluded_prereleases": excluded_prereleases,
                "excluded_unusable": excluded_unusable})

    _latest_version, latest_key = max(candidates, key=lambda pair: pair[0])
    context: Dict[str, Any] = {
        "candidate_count": len(candidates),
        "excluded_prereleases": excluded_prereleases,
        "excluded_unusable": excluded_unusable,
        "invalid_version_keys": invalid_keys[:_MAX_CONTEXT_ITEMS],
        "invalid_version_keys_truncated":
            len(invalid_keys) > _MAX_CONTEXT_ITEMS,
    }
    info = payload.get("info")
    if isinstance(info, dict):
        raw_info_version = info.get("version")
        try:
            context["info_version_matches"] = (
                Version(str(raw_info_version)) == _latest_version)
        except InvalidVersion:
            context["info_version_matches"] = False
    return latest_key, context


def resolve_installed_version(
    python_path: Optional[str],
    *,
    execute: Optional[Callable[..., Any]] = None,
) -> Tuple[Optional[str], Optional[UpdateDiscoveryError]]:
    """Resolve the installed version via the existing DEC-A probe (spec §6.2).

    *python_path* is the explicitly configured interpreter (a snapshot taken
    by the caller); ``None``/blank means unconfigured. The probe itself is
    ``query_installed_core_version`` — never duplicated here — and is bounded
    by its existing 5 s subprocess cap. Any unresolved outcome yields the
    local ``INSTALLED_UNKNOWN`` error without hiding any remote result.
    """
    if not isinstance(python_path, str) or not python_path.strip():
        return None, UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.INSTALLED_UNKNOWN,
            technical_context={"cause": "no_interpreter_configured"})
    version = query_installed_core_version(python_path, execute=execute)
    if version is None:
        return None, UpdateDiscoveryError(
            UpdateDiscoveryFailureReason.INSTALLED_UNKNOWN,
            technical_context={"cause": "probe_failed",
                               "python_path": python_path})
    return version, None


def classify_update_discovery(
    *,
    installed_version: Optional[str],
    latest_stable: Optional[str],
    admitted_versions: FrozenSet[str],
) -> CoreUpdateDiscoveryState:
    """Pure classification over explicit inputs (spec §7, fail-closed).

    * ``latest_stable is None`` → ``SEARCH_FAILED`` (no remote conclusion);
    * unresolvable/unparseable installed version → ``INSTALLED_UNKNOWN``;
    * an unparseable ``latest_stable`` input → ``SEARCH_FAILED`` (the remote
      side cannot be trusted);
    * admission membership is an **exact string** check against the
      allowlist snapshot, mirroring DEC-A's exact-version policy — comparison
      for ordering is delegated to ``packaging``.
    """
    if latest_stable is None:
        return CoreUpdateDiscoveryState.SEARCH_FAILED
    if installed_version is None:
        return CoreUpdateDiscoveryState.INSTALLED_UNKNOWN
    try:
        latest = Version(latest_stable)
    except InvalidVersion:
        return CoreUpdateDiscoveryState.SEARCH_FAILED
    try:
        installed = Version(installed_version)
    except InvalidVersion:
        return CoreUpdateDiscoveryState.INSTALLED_UNKNOWN
    if installed == latest:
        return CoreUpdateDiscoveryState.LATEST_INSTALLED
    if latest > installed:
        if latest_stable in admitted_versions:
            return CoreUpdateDiscoveryState.NEWER_STABLE_ADMITTED
        return CoreUpdateDiscoveryState.NEWER_STABLE_NOT_ADMITTED
    return CoreUpdateDiscoveryState.INSTALLED_AHEAD


def discover_core_update(
    *,
    python_path: Optional[str],
    admitted_versions: FrozenSet[str] = SUPPORTED_CORE_VERSIONS,
    fetch: Optional[Callable[..., bytes]] = None,
    version_execute: Optional[Callable[..., Any]] = None,
    now: Callable[[], float] = time.monotonic,
    timeout_seconds: float = MAX_UPDATE_CHECK_TIMEOUT_SECONDS,
) -> UpdateDiscoveryResult:
    """Run one manual discovery (spec §6.2) and classify the outcome.

    *python_path* is the explicitly configured interpreter path snapshotted
    by the caller (``None`` when unconfigured — the PyPI query still runs);
    *admitted_versions* is the read-only allowlist snapshot (defaults to the
    real ``SUPPORTED_CORE_VERSIONS`` and is never written); *fetch* replaces
    the network transport for tests (called as ``fetch(deadline=...)``,
    returning the raw body or raising :class:`UpdateDiscoveryError`);
    *version_execute* and *now* are the existing injectable seams.

    The absolute total deadline (``timeout_seconds`` from *now*) covers the
    installed-version probe indirectly (its own 5 s cap) and the network
    fetch exactly; a fetch that finishes after the deadline raises
    ``TIMEOUT``. Stale-result dropping and single-flight UI concurrency are
    Stage-3 duties; this function performs exactly one bounded operation.
    """
    if timeout_seconds is None or not (0 < float(timeout_seconds)):
        raise ValueError("timeout_seconds must be a positive number.")
    admitted = frozenset(admitted_versions)

    # 1-2) Snapshot inputs are the arguments themselves; resolve installed.
    installed, installed_failure = resolve_installed_version(
        python_path, execute=version_execute)

    # 3) One PyPI query under a single absolute deadline computed here.
    deadline = now() + float(timeout_seconds)
    fetch_call = fetch if fetch is not None else fetch_pypi_project_body
    latest: Optional[str] = None
    selection_context: Dict[str, Any] = {}
    search_failure: Optional[UpdateDiscoveryError] = None
    try:
        body = fetch_call(deadline=deadline)
        latest, selection_context = select_latest_stable_version(body)
    except UpdateDiscoveryError as exc:
        search_failure = exc

    # 4-7) Pure validation/selection happened above; classify and report.
    state = classify_update_discovery(
        installed_version=installed,
        latest_stable=latest,
        admitted_versions=admitted)
    return UpdateDiscoveryResult(
        state=state,
        installed_version=installed,
        latest_stable=latest,
        admitted_versions=admitted,
        search_failure=search_failure,
        installed_failure=installed_failure,
        technical_context=selection_context)


__all__ = [
    "CORE_UPDATE_DISCOVERY_STATES",
    "MAX_UPDATE_CHECK_TIMEOUT_SECONDS",
    "MAX_UPDATE_RESPONSE_BYTES",
    "PYPI_HOST",
    "PYPI_PROJECT_JSON_URL",
    "CoreUpdateDiscoveryState",
    "UpdateDiscoveryError",
    "UpdateDiscoveryFailureReason",
    "UpdateDiscoveryResult",
    "classify_update_discovery",
    "discover_core_update",
    "fetch_pypi_project_body",
    "resolve_installed_version",
    "select_latest_stable_version",
]
