"""WP1 — Interpreter validation executor (DEC-I / ADR-0001 Amendment A1).

Runs the *exact* diagnostic recorded in ADR-0001 Amendment A1 against the
explicitly selected absolute Python executable and delegates outcome
classification to the existing pure contract
``castlearq_desktop.core.contracts.interpreter.classify_validation_result``.

Fixed policy, per the approved decisions:

* separated argument list, ``shell=False`` — no shell is ever invoked;
* isolated mode (``-I``): user site-packages and ``PYTHONPATH`` are intentionally
  excluded, so installations discoverable only that way are rejected as
  ``CORE_NOT_INSTALLED`` — a deliberate fail-closed choice (Amendment A1);
* exactly one attempt, hard timeout of at most five seconds, no retries;
* Core is never imported or executed, nothing is installed, nothing is
  persisted, and no network access occurs.

Tests inject a fake executor; no real subprocess is ever required.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Callable, List, Optional

from castlearq_desktop.core.contracts.errors import ErrorCode
from castlearq_desktop.core.contracts.interpreter import (
    ISOLATED_MODE_FLAG,
    MAXIMUM_VALIDATION_TIMEOUT_SECONDS,
    InterpreterValidationResult,
    ValidationOutcome,
    classify_validation_result,
)

#: The exact A1 diagnostic source (ADR-0001 section A1.2), reproduced verbatim.
DIAGNOSTIC_SOURCE = """\
import importlib.metadata as metadata
import importlib.util
import json
import sys

try:
    metadata.distribution("castlearq")
    distribution_found = True
except metadata.PackageNotFoundError:
    distribution_found = False

try:
    package_found = importlib.util.find_spec("castlearq") is not None
except (ImportError, ValueError):
    package_found = False

print(json.dumps({
    "python_version": list(sys.version_info[:3]),
    "distribution_found": distribution_found,
    "package_found": package_found,
}, separators=(",", ":")))"""


def _rejected(detail: str) -> ValidationOutcome:
    return ValidationOutcome(code=ErrorCode.INTERPRETER_INVALID, detail=detail)


def build_validation_command(python_path: str) -> List[str]:
    """Return ``[python, -I, -c, DIAGNOSTIC_SOURCE]`` for an absolute *python_path*.

    Raises ``ValueError`` for a non-absolute or empty path so that a selection
    that is not an explicit absolute executable path is rejected *before* any
    process is executed.
    """
    if not isinstance(python_path, str) or not python_path.strip():
        raise ValueError("An interpreter path must be a non-empty string.")
    if not os.path.isabs(python_path):
        raise ValueError(
            "An interpreter path must be absolute; no PATH lookup is performed."
        )
    return [python_path, ISOLATED_MODE_FLAG, "-c", DIAGNOSTIC_SOURCE]


def validate_python_interpreter(
    python_path: str,
    *,
    execute: Optional[Callable[..., Any]] = None,
) -> ValidationOutcome:
    """Run the A1 diagnostic on *python_path* and classify the observed result.

    ``execute`` defaults to :func:`subprocess.run`; tests inject a stub. The
    diagnostic is executed **exactly once** (``VALIDATION_ATTEMPTS == 1``) with a
    hard timeout of ``MAXIMUM_VALIDATION_TIMEOUT_SECONDS``; there are no retries
    and no fallback interpreters.
    """
    try:
        command = build_validation_command(python_path)
    except ValueError as exc:
        return _rejected(str(exc))

    if execute is None:
        execute = subprocess.run

    try:
        # DEC-I: exactly one attempt (contract VALIDATION_ATTEMPTS == 1); the
        # single explicit invocation below is the whole policy — no retry loop.
        completed = execute(
            command,
            capture_output=True,
            text=True,
            shell=False,
            timeout=MAXIMUM_VALIDATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        result = InterpreterValidationResult(timed_out=True)
    except Exception:  # fail closed: any execution problem is INTERPRETER_INVALID
        result = InterpreterValidationResult(execution_failed=True)
    else:
        result = InterpreterValidationResult(
            exit_status=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    return classify_validation_result(result)


__all__ = [
    "DIAGNOSTIC_SOURCE",
    "build_validation_command",
    "validate_python_interpreter",
]
