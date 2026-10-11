"""WP0-F — Interpreter-validation output classifier (pure).

Implements the DEC-I output contract as a pure classifier over a *synthetic*
validation result. No subprocess is executed, no interpreter is inspected, Core
is never imported, no package is installed, and no result is persisted.

The exact diagnostic command text is **unspecified** by the approved policy and
is therefore neither defined nor asserted here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from castlearq_desktop.core.contracts.errors import ErrorCode

#: Minimum accepted Python version (DEC-I: at least 3.11).
MINIMUM_PYTHON_VERSION = (3, 11)

#: Maximum subprocess timeout, in seconds (DEC-I: 5 s, one attempt).
MAXIMUM_VALIDATION_TIMEOUT_SECONDS = 5.0

#: Exactly one attempt per explicit validation request (DEC-I).
VALIDATION_ATTEMPTS = 1

#: Isolated-mode flag required by DEC-I; recorded, never asserted as a command.
ISOLATED_MODE_FLAG = "-I"

#: The exact set of permitted output fields (DEC-I).
REQUIRED_OUTPUT_FIELDS = ("python_version", "distribution_found", "package_found")

#: Number of components required in ``python_version``.
PYTHON_VERSION_COMPONENT_COUNT = 3


@dataclass(frozen=True)
class ValidationOutcome:
    """Result of classifying a synthetic interpreter-validation result."""

    code: Optional[ErrorCode]
    python_version: Optional[tuple] = None
    distribution_found: Optional[bool] = None
    package_found: Optional[bool] = None
    detail: str = ""

    @property
    def is_valid(self) -> bool:
        """Whether the interpreter is usable (no error code assigned)."""
        return self.code is None

    def technical_context(self) -> Dict[str, Any]:
        """Structured, traceback-free diagnostic payload."""
        return {
            "code": self.code.value if self.code else None,
            "python_version": list(self.python_version) if self.python_version else None,
            "distribution_found": self.distribution_found,
            "package_found": self.package_found,
            "detail": self.detail,
        }


def _invalid(detail: str) -> ValidationOutcome:
    return ValidationOutcome(code=ErrorCode.INTERPRETER_INVALID, detail=detail)


def _is_exact_int(value: Any) -> bool:
    """Whether *value* is an integer and not a ``bool`` (which subclasses int)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_python_version(value: Any) -> Optional[tuple]:
    """Return a 3-tuple of ints, or ``None`` when *value* is malformed."""
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) != PYTHON_VERSION_COMPONENT_COUNT:
        return None
    if not all(_is_exact_int(part) for part in value):
        return None
    return tuple(int(part) for part in value)


def version_is_supported(python_version: tuple) -> bool:
    """Whether *python_version* meets the minimum accepted version (DEC-I)."""
    return tuple(python_version) >= MINIMUM_PYTHON_VERSION


@dataclass(frozen=True)
class InterpreterValidationResult:
    """A synthetic interpreter-validation result, as if produced by a subprocess.

    ``stdout`` is the text the validation command would have written;
    ``exit_status`` is the status it would have returned. ``timed_out`` and
    ``execution_failed`` model the two failure modes that produce no usable
    output at all.
    """

    exit_status: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    execution_failed: bool = False


def _classify_payload(
    result: InterpreterValidationResult,
    payload: Any,
) -> ValidationOutcome:
    """Classify a parsed JSON payload against the approved output schema."""
    if not isinstance(payload, Mapping):
        return _invalid("validation output was not a JSON object")

    keys = set(payload.keys())
    expected = set(REQUIRED_OUTPUT_FIELDS)
    missing = sorted(expected - keys)
    if missing:
        return _invalid(f"validation output is missing fields: {', '.join(missing)}")
    extra = sorted(keys - expected)
    if extra:
        return _invalid(f"validation output has unexpected fields: {', '.join(extra)}")

    python_version = _parse_python_version(payload["python_version"])
    if python_version is None:
        return _invalid(
            "python_version must be exactly three integers, "
            f"got {payload['python_version']!r}"
        )

    distribution_found = payload["distribution_found"]
    package_found = payload["package_found"]
    if not isinstance(distribution_found, bool):
        return _invalid(f"distribution_found must be a boolean, got {distribution_found!r}")
    if not isinstance(package_found, bool):
        return _invalid(f"package_found must be a boolean, got {package_found!r}")

    if not version_is_supported(python_version):
        return ValidationOutcome(
            code=ErrorCode.INTERPRETER_INVALID,
            python_version=python_version,
            distribution_found=distribution_found,
            package_found=package_found,
            detail=(
                "python version "
                f"{'.'.join(str(p) for p in python_version)} is below the required "
                f"{MINIMUM_PYTHON_VERSION[0]}.{MINIMUM_PYTHON_VERSION[1]}"
            ),
        )

    if not distribution_found or not package_found:
        return ValidationOutcome(
            code=ErrorCode.CORE_NOT_INSTALLED,
            python_version=python_version,
            distribution_found=distribution_found,
            package_found=package_found,
            detail=(
                "castlearq distribution or package was not found in the selected "
                "interpreter"
            ),
        )

    return ValidationOutcome(
        code=None,
        python_version=python_version,
        distribution_found=distribution_found,
        package_found=package_found,
        detail="interpreter validation succeeded",
    )


def classify_validation_result(result: InterpreterValidationResult) -> ValidationOutcome:
    """Classify a synthetic validation result per DEC-I. Pure; runs no subprocess.

    Mapping:

    * execution failure, timeout, non-zero exit status, malformed or incomplete
      JSON, wrong field set, or invalid field types → ``INTERPRETER_INVALID``;
    * a supported Python version but a missing distribution or package →
      ``CORE_NOT_INSTALLED``;
    * otherwise → valid (no error code).
    """
    if result.execution_failed:
        return _invalid("the validation command could not be executed")
    if result.timed_out:
        return _invalid(
            "the validation command timed out after "
            f"{MAXIMUM_VALIDATION_TIMEOUT_SECONDS} seconds"
        )
    if result.exit_status is None:
        return _invalid("no exit status was observed for the validation command")
    if result.exit_status != 0:
        return _invalid(f"the validation command exited with status {result.exit_status}")

    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        return _invalid("the validation command did not produce valid JSON")

    return _classify_payload(result, payload)


__all__ = [
    "ISOLATED_MODE_FLAG",
    "InterpreterValidationResult",
    "MAXIMUM_VALIDATION_TIMEOUT_SECONDS",
    "MINIMUM_PYTHON_VERSION",
    "PYTHON_VERSION_COMPONENT_COUNT",
    "REQUIRED_OUTPUT_FIELDS",
    "VALIDATION_ATTEMPTS",
    "ValidationOutcome",
    "classify_validation_result",
    "version_is_supported",
]
