# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0 (the "License").

"""DEC-A -- conservative Core version admission (pure + isolated query)."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable, FrozenSet, Mapping, Optional, Tuple

from castlearq_desktop.core.contracts.interpreter import (
    MAXIMUM_VALIDATION_TIMEOUT_SECONDS,
)
from castlearq_desktop.core.contracts.readiness import VersionAdmission

#: Centralized explicit allowlist of admitted Core versions (DEC-A).
#: Exact version strings only (no ranges, no wildcards). ``0.5.1`` was
#: explicitly admitted after real verification against an isolated Core
#: 0.5.1 installation (the really-installed version equals the real
#: ``/health`` version and readiness succeeds).
SUPPORTED_CORE_VERSIONS: FrozenSet[str] = frozenset({"0.5.1", "0.6.0", "0.6.1"})

#: Version covered by the current DEC-A admission decision: ``0.5.1`` is
#: explicitly admitted (see ``SUPPORTED_CORE_VERSIONS``).
CANDIDATE_VERSION = "0.6.1"

#: Approved top-level JSON field carrying the Core version in GET /health.
HEALTH_VERSION_FIELD = "version"

#: Hard cap for a decoded GET /health body (avoid unbounded buffering).
MAX_HEALTH_BODY_BYTES = 64 * 1024

#: Source executed on the explicitly selected interpreter to report the
#: installed ``castlearq`` distribution version. Stdlib only; never imports
#: or executes ``castlearq``; prints exactly one JSON object.
INSTALLED_VERSION_SOURCE = """\
import importlib.metadata as metadata
import json

print(json.dumps({"distribution_version": metadata.version("castlearq")}, separators=(",", ":")))"""


def is_valid_version(value: Any) -> bool:
    """Return True only for a strict, conservative version string."""
    if not isinstance(value, str):
        return False
    if not value or value != value.strip():
        return False
    if len(value) > 64:
        return False
    if any(ch.isspace() for ch in value):
        return False
    allowed = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_+"
    )
    return all(ch in allowed for ch in value)


def build_installed_version_command(python_path: str) -> list:
    """Return [python, -I, -c, INSTALLED_VERSION_SOURCE]."""
    if not isinstance(python_path, str) or not python_path.strip():
        raise ValueError("An interpreter path must be a non-empty string.")
    if not os.path.isabs(python_path):
        raise ValueError(
            "An interpreter path must be absolute; no PATH lookup is performed."
        )
    return [python_path, "-I", "-c", INSTALLED_VERSION_SOURCE]

def parse_installed_version_output(stdout: str) -> Optional[str]:
    """Extract the distribution version string, or None when invalid."""
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if set(payload.keys()) != {"distribution_version"}:
        return None
    version = payload.get("distribution_version")
    return version if is_valid_version(version) else None


def query_installed_core_version(
    python_path: str,
    *,
    execute: Optional[Callable[..., Any]] = None,
) -> Optional[str]:
    """Query installed ``castlearq`` version with the selected interpreter.

    Uses only *python_path* (absolute, no PATH fallback). Any execution
    problem, non-zero exit, invalid JSON, or invalid version yields None.
    """
    try:
        command = build_installed_version_command(python_path)
    except ValueError:
        return None
    if execute is None:
        execute = subprocess.run
    try:
        completed = execute(
            command,
            capture_output=True,
            text=True,
            shell=False,
            timeout=MAXIMUM_VALIDATION_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    try:
        if completed.returncode != 0:
            return None
        return parse_installed_version_output(completed.stdout or "")
    except Exception:
        return None


def extract_health_version(body: Any) -> Tuple[bool, Optional[str]]:
    """Extract top-level ``version`` string from a /health body.

    Returns (present, version); (False, None) means absent/undecodable.
    Accepts a decoded object, a JSON string, or UTF-8 bytes.
    """
    raw: Any = body
    if isinstance(body, (bytes, bytearray)):
        if len(body) > MAX_HEALTH_BODY_BYTES:
            return (False, None)
        try:
            raw = bytes(body).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return (False, None)
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_HEALTH_BODY_BYTES:
            return (False, None)
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return (False, None)
    if isinstance(raw, Mapping):
        if HEALTH_VERSION_FIELD not in raw:
            return (False, None)
        return (True, raw.get(HEALTH_VERSION_FIELD))
    return (False, None)


def classify_version_admission(
    installed_version: Any,
    health_version: Any,
    *,
    health_present: bool = True,
    supported_versions: FrozenSet[str] = SUPPORTED_CORE_VERSIONS,
) -> VersionAdmission:
    """Classify admission over explicit version inputs (pure, fail-closed)."""
    if not is_valid_version(installed_version):
        return VersionAdmission.MISSING
    if not health_present:
        return VersionAdmission.MISSING
    if not is_valid_version(health_version):
        return VersionAdmission.MALFORMED
    if installed_version != health_version:
        return VersionAdmission.INCOMPATIBLE
    if health_version in supported_versions:
        return VersionAdmission.ADMITTED
    return VersionAdmission.UNKNOWN


def evaluate_version_admission(
    installed_version: Any,
    health_body: Any,
    *,
    supported_versions: FrozenSet[str] = SUPPORTED_CORE_VERSIONS,
) -> VersionAdmission:
    """Evaluate DEC-A admission from an installed version and health body."""
    present, health_version = extract_health_version(health_body)
    return classify_version_admission(
        installed_version,
        health_version,
        health_present=present,
        supported_versions=supported_versions,
    )


__all__ = [
    "CANDIDATE_VERSION",
    "HEALTH_VERSION_FIELD",
    "INSTALLED_VERSION_SOURCE",
    "MAX_HEALTH_BODY_BYTES",
    "SUPPORTED_CORE_VERSIONS",
    "build_installed_version_command",
    "classify_version_admission",
    "evaluate_version_admission",
    "extract_health_version",
    "is_valid_version",
    "parse_installed_version_output",
    "query_installed_core_version",
]
