# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0

"""Managed Python environment discovery and validation for CastleArq Desktop."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QStandardPaths

from castlearq_desktop.core.interpreter_validator import validate_python_interpreter
from castlearq_desktop.core.version_admission import (
    SUPPORTED_CORE_VERSIONS,
    query_installed_core_version,
)

# Sentinel: "no resultado provisto, ejecutar provider habitual"
_UNSET = object()


class ManagedEnvironmentStatus(Enum):
    MISSING = "missing"
    PYTHON_NOT_EXECUTABLE = "python_not_executable"
    PYTHON_INVALID = "python_invalid"
    CORE_NOT_INSTALLED = "core_not_installed"
    VERSION_INCOMPATIBLE = "version_incompatible"
    VALID = "valid"


@dataclass(frozen=True)
class ManagedEnvironmentResult:
    root_path: Path
    python_path: Optional[Path]
    status: ManagedEnvironmentStatus
    detected_version: Optional[str]
    reason: str


def managed_environment_root() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    return Path(base) / "managed-python"


def managed_python_executable(root: Path) -> Path:
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def discover_managed_environment(root_override: Optional[Path] = None) -> ManagedEnvironmentResult:
    root = root_override if root_override is not None else managed_environment_root()
    python = managed_python_executable(root)

    if not root.exists():
        return ManagedEnvironmentResult(
            root_path=root,
            python_path=python,
            status=ManagedEnvironmentStatus.MISSING,
            detected_version=None,
            reason="managed environment directory does not exist",
        )

    if not python.is_file() or not os.access(python, os.X_OK):
        return ManagedEnvironmentResult(
            root_path=root,
            python_path=python,
            status=ManagedEnvironmentStatus.PYTHON_NOT_EXECUTABLE,
            detected_version=None,
            reason="managed Python executable is missing or not executable",
        )

    outcome = validate_python_interpreter(str(python))
    if not outcome.is_valid:
        code = outcome.code.value if outcome.code is not None else ""
        if code == "CORE_NOT_INSTALLED":
            return ManagedEnvironmentResult(
                root_path=root,
                python_path=python,
                status=ManagedEnvironmentStatus.CORE_NOT_INSTALLED,
                detected_version=None,
                reason=outcome.detail or "Core is not installed",
            )
        return ManagedEnvironmentResult(
            root_path=root,
            python_path=python,
            status=ManagedEnvironmentStatus.PYTHON_INVALID,
            detected_version=None,
            reason=outcome.detail or "Python interpreter validation failed",
        )

    version = query_installed_core_version(str(python))
    if not version:
        return ManagedEnvironmentResult(
            root_path=root,
            python_path=python,
            status=ManagedEnvironmentStatus.CORE_NOT_INSTALLED,
            detected_version=None,
            reason="Core is not installed in the managed environment",
        )

    if version not in SUPPORTED_CORE_VERSIONS:
        return ManagedEnvironmentResult(
            root_path=root,
            python_path=python,
            status=ManagedEnvironmentStatus.VERSION_INCOMPATIBLE,
            detected_version=version,
            reason=f"Core version {version} is not admitted",
        )

    return ManagedEnvironmentResult(
        root_path=root,
        python_path=python,
        status=ManagedEnvironmentStatus.VALID,
        detected_version=version,
        reason="managed environment is valid",
    )



def resolve_installed_core_version(
    *,
    managed_environment_provider: Optional[Callable[[], Any]] = None,
    settings: Any = None,
    version_query: Optional[Callable[[str], Optional[str]]] = None,
    managed_result: Optional[ManagedEnvironmentResult] = _UNSET,
) -> Optional[str]:
    """Single source of truth for the installed Core version.

    Managed VALID wins. MISSING/CORE_NOT_INSTALLED allow manual fallback.
    Other managed states block fallback and return None.

    Si ``managed_result`` es ``_UNSET`` (default), se ejecuta el provider
    normalmente, conservando compatibilidad con callers externos. Si se provee
    un resultado, se reutiliza directamente, evitando segundo discovery.

    Estados:
      - VALID → usar versión gestionada.
      - MISSING / CORE_NOT_INSTALLED → permitir fallback manual.
      - PYTHON_NOT_EXECUTABLE / PYTHON_INVALID / VERSION_INCOMPATIBLE → bloquear fallback.
    """
    # Si se proveyó un resultado, úsalo directamente (evita rediscovery).
    if managed_result is not _UNSET:
        managed = managed_result
    else:
        # Llamada sin resultado proveído: comportamiento original, compatibilidad total.
        provider = managed_environment_provider
        if provider is None:
            provider = discover_managed_environment
        try:
            managed = provider()
        except Exception:
            managed = None

    status = getattr(managed, "status", None)
    if status is ManagedEnvironmentStatus.VALID:
        return getattr(managed, "detected_version", None)
    if status in (ManagedEnvironmentStatus.MISSING, ManagedEnvironmentStatus.CORE_NOT_INSTALLED):
        if settings is None:
            return None
        try:
            path = settings.load()
        except Exception:
            path = None
        if not isinstance(path, str) or not path.strip():
            return None
        q = version_query if version_query is not None else query_installed_core_version
        try:
            return q(path)
        except Exception:
            return None
    # PYTHON_NOT_EXECUTABLE, PYTHON_INVALID, VERSION_INCOMPATIBLE → None (bloquean fallback)
    return None
