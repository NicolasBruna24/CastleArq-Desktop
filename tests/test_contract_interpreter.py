"""WP0-F contract tests: interpreter-validation output classifier.

Table-driven tests over synthetic results. No subprocess is executed and no
interpreter is inspected.
"""

from __future__ import annotations

import json
import sys

import pytest

from castlearq_desktop.core.contracts import (
    ISOLATED_MODE_FLAG,
    MAXIMUM_VALIDATION_TIMEOUT_SECONDS,
    MINIMUM_PYTHON_VERSION,
    REQUIRED_OUTPUT_FIELDS,
    VALIDATION_ATTEMPTS,
    InterpreterValidationResult,
    classify_validation_result,
    version_is_supported,
)
from castlearq_desktop.core.contracts.errors import ErrorCode


def valid_payload() -> dict:
    """A complete, well-formed validation payload."""
    return {
        "python_version": [3, 11, 0],
        "distribution_found": True,
        "package_found": True,
    }


def result_with(payload: object, exit_status: int = 0) -> InterpreterValidationResult:
    """Build a synthetic result carrying *payload* as its stdout."""
    if isinstance(payload, str):
        stdout = payload
    else:
        stdout = json.dumps(payload)
    return InterpreterValidationResult(exit_status=exit_status, stdout=stdout)


# --- Approved constants (DEC-I) -------------------------------------------


def test_approved_validation_constants() -> None:
    """The DEC-I constants must match the approved values exactly."""
    assert MINIMUM_PYTHON_VERSION == (3, 11)
    assert MAXIMUM_VALIDATION_TIMEOUT_SECONDS == 5.0
    assert VALIDATION_ATTEMPTS == 1
    assert ISOLATED_MODE_FLAG == "-I"
    assert set(REQUIRED_OUTPUT_FIELDS) == {
        "python_version",
        "distribution_found",
        "package_found",
    }


# --- Valid output ---------------------------------------------------------


def test_valid_output_is_accepted() -> None:
    """Complete, well-formed output with everything present is valid."""
    outcome = classify_validation_result(result_with(valid_payload()))
    assert outcome.is_valid
    assert outcome.code is None
    assert outcome.python_version == (3, 11, 0)
    assert outcome.distribution_found is True
    assert outcome.package_found is True


@pytest.mark.parametrize("version", [[3, 11, 0], [3, 12, 1], [3, 14, 4], [4, 0, 0]])
def test_supported_python_versions_are_accepted(version: list) -> None:
    outcome = classify_validation_result(
        result_with({**valid_payload(), "python_version": version})
    )
    assert outcome.is_valid


@pytest.mark.parametrize("version", [[3, 10, 9], [3, 9, 0], [2, 7, 18], [3, 10, 999]])
def test_unsupported_python_versions_are_interpreter_invalid(version: list) -> None:
    """A version below the minimum is INTERPRETER_INVALID, not CORE_NOT_INSTALLED."""
    outcome = classify_validation_result(
        result_with({**valid_payload(), "python_version": version})
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


def test_version_comparison_is_componentwise() -> None:
    """3.11.0 is the exact boundary and must be accepted."""
    assert version_is_supported((3, 11, 0))
    assert not version_is_supported((3, 10, 99))


# --- Execution failures, timeouts, and exit status ------------------------


def test_execution_failure_is_interpreter_invalid() -> None:
    outcome = classify_validation_result(
        InterpreterValidationResult(execution_failed=True, stderr="no such file")
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


def test_timeout_is_interpreter_invalid() -> None:
    outcome = classify_validation_result(
        InterpreterValidationResult(timed_out=True)
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


@pytest.mark.parametrize("exit_status", [1, 2, 127])
def test_non_zero_exit_status_is_interpreter_invalid(exit_status: int) -> None:
    outcome = classify_validation_result(
        result_with(valid_payload(), exit_status=exit_status)
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


def test_missing_exit_status_is_interpreter_invalid() -> None:
    """With no observed exit status there is no evidence of success."""
    outcome = classify_validation_result(
        InterpreterValidationResult(exit_status=None, stdout="{}")
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


# --- Malformed and incomplete JSON ---------------------------------------


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not json",
        "{",
        '{"python_version": [3, 11, 0]',
        "[]",
        "null",
        '"a string"',
        "42",
    ],
)
def test_malformed_json_is_interpreter_invalid(stdout: str) -> None:
    outcome = classify_validation_result(
        InterpreterValidationResult(exit_status=0, stdout=stdout)
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


@pytest.mark.parametrize(
    "missing_field",
    ["python_version", "distribution_found", "package_found"],
)
def test_missing_field_is_interpreter_invalid(missing_field: str) -> None:
    payload = {k: v for k, v in valid_payload().items() if k != missing_field}
    outcome = classify_validation_result(result_with(payload))
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


def test_extra_field_is_interpreter_invalid() -> None:
    """The schema must contain exactly the expected fields."""
    outcome = classify_validation_result(
        result_with({**valid_payload(), "extra": "value"})
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


# --- Invalid field types --------------------------------------------------


@pytest.mark.parametrize(
    "version",
    [
        "3.11.0",
        [3, 11],
        [3, 11, 0, 0],
        [3, "11", 0],
        [3, 11, None],
        [3.0, 11.0, 0.0],
        [True, 11, 0],
        None,
        {"major": 3},
    ],
)
def test_invalid_python_version_types_are_interpreter_invalid(version: object) -> None:
    """python_version must be exactly three true integers."""
    outcome = classify_validation_result(
        result_with({**valid_payload(), "python_version": version})
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


@pytest.mark.parametrize(
    "flag",
    ["true", 1, 0, None, [], "yes"],
)
def test_invalid_distribution_found_type_is_interpreter_invalid(flag: object) -> None:
    """distribution_found must be a real boolean."""
    outcome = classify_validation_result(
        result_with({**valid_payload(), "distribution_found": flag})
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


@pytest.mark.parametrize(
    "flag",
    ["true", 1, 0, None, [], "yes"],
)
def test_invalid_package_found_type_is_interpreter_invalid(flag: object) -> None:
    """package_found must be a real boolean."""
    outcome = classify_validation_result(
        result_with({**valid_payload(), "package_found": flag})
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


# --- Missing distribution or package -------------------------------------


def test_missing_distribution_is_core_not_installed() -> None:
    """Valid output showing a missing distribution is CORE_NOT_INSTALLED."""
    outcome = classify_validation_result(
        result_with({**valid_payload(), "distribution_found": False})
    )
    assert outcome.code is ErrorCode.CORE_NOT_INSTALLED
    assert outcome.python_version == (3, 11, 0)
    assert outcome.distribution_found is False


def test_missing_package_is_core_not_installed() -> None:
    """Valid output showing a missing package is CORE_NOT_INSTALLED."""
    outcome = classify_validation_result(
        result_with({**valid_payload(), "package_found": False})
    )
    assert outcome.code is ErrorCode.CORE_NOT_INSTALLED
    assert outcome.package_found is False


def test_missing_both_is_core_not_installed() -> None:
    outcome = classify_validation_result(
        result_with(
            {**valid_payload(), "distribution_found": False, "package_found": False}
        )
    )
    assert outcome.code is ErrorCode.CORE_NOT_INSTALLED


def test_unsupported_version_takes_precedence_over_core_not_installed() -> None:
    """An incompatible interpreter is INTERPRETER_INVALID regardless of Core."""
    outcome = classify_validation_result(
        result_with(
            {
                "python_version": [3, 10, 0],
                "distribution_found": False,
                "package_found": False,
            }
        )
    )
    assert outcome.code is ErrorCode.INTERPRETER_INVALID


def test_diagnostic_context_is_structured() -> None:
    outcome = classify_validation_result(
        result_with({**valid_payload(), "distribution_found": False})
    )
    context = outcome.technical_context()
    assert context["code"] == "CORE_NOT_INSTALLED"
    assert context["python_version"] == [3, 11, 0]
    assert context["distribution_found"] is False
    assert "Traceback" not in repr(context)


def test_classifier_runs_no_subprocess() -> None:
    """Classification must succeed with no interpreter present.

    The classifier is pure: a synthetic result is classified without invoking
    any executable, and no module of the Core application is imported.
    """
    outcome = classify_validation_result(result_with(valid_payload()))
    assert outcome.is_valid
    assert "castlearq.main" not in sys.modules
