"""WP1 tests: interpreter validation executor.

Every test injects a stub executor; no real subprocess is run, no real
interpreter is inspected, CastleArq Core is never imported or launched, and no
network access occurs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from castlearq_desktop.core.contracts.interpreter import (
    ISOLATED_MODE_FLAG,
    MAXIMUM_VALIDATION_TIMEOUT_SECONDS,
    VALIDATION_ATTEMPTS,
)
from castlearq_desktop.core.contracts.errors import ErrorCode
from castlearq_desktop.core.interpreter_validator import (
    DIAGNOSTIC_SOURCE,
    build_validation_command,
    validate_python_interpreter,
)

ABS_PYTHON = "/abs/env/bin/python"


class StubExecutor:
    """Records every call and returns or raises as configured."""

    def __init__(self, *, returncode=0, stdout="", stderr="", raises=None):
        self.calls: list = []
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._raises = raises

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self._raises is not None:
            raise self._raises
        return types.SimpleNamespace(
            returncode=self._returncode, stdout=self._stdout, stderr=self._stderr
        )


def valid_stdout() -> str:
    return json.dumps(
        {
            "python_version": [3, 12, 1],
            "distribution_found": True,
            "package_found": True,
        }
    )


# --- Command construction (AC3, AC4) ---------------------------------------


def test_command_is_executable_flag_c_and_the_a1_source() -> None:
    command = build_validation_command(ABS_PYTHON)
    assert command == [ABS_PYTHON, ISOLATED_MODE_FLAG, "-c", DIAGNOSTIC_SOURCE]
    assert command[1] == "-I"
    assert command[2] == "-c"


def test_command_uses_a_separated_string_argument_list() -> None:
    command = build_validation_command(ABS_PYTHON)
    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)


def test_diagnostic_source_is_the_exact_a1_block() -> None:
    """The executed source must be byte-identical to ADR-0001 section A1.2."""
    adr = (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "adr-0001-core-launch-contract.md"
    ).read_text(encoding="utf-8")
    section = adr.split("### A1.2", 1)[1]
    fenced = section.split("```python\n", 1)[1].split("```", 1)[0]
    assert fenced.rstrip("\n") == DIAGNOSTIC_SOURCE


def test_diagnostic_never_imports_or_launches_core() -> None:
    assert "import castlearq" not in DIAGNOSTIC_SOURCE
    assert "castlearq.main" not in DIAGNOSTIC_SOURCE
    assert "importlib.import_module" not in DIAGNOSTIC_SOURCE


# --- Execution policy (AC3, AC6, AC7) --------------------------------------


def test_success_uses_one_attempt_with_shell_false_and_five_second_timeout() -> None:
    executor = StubExecutor(stdout=valid_stdout())
    outcome = validate_python_interpreter(ABS_PYTHON, execute=executor)

    assert outcome.is_valid
    assert len(executor.calls) == 1
    command, kwargs = executor.calls[0]
    assert command[0] == ABS_PYTHON
    assert kwargs["shell"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == MAXIMUM_VALIDATION_TIMEOUT_SECONDS
    assert kwargs["timeout"] <= 5.0
    assert VALIDATION_ATTEMPTS == 1


def test_no_core_import_occurs_in_the_desktop_process() -> None:
    executor = StubExecutor(stdout=valid_stdout())
    validate_python_interpreter(ABS_PYTHON, execute=executor)
    assert "castlearq" not in sys.modules
    assert "castlearq.main" not in sys.modules


# --- Outcome mapping through the existing classifier (AC5) -----------------


def test_successful_exit_and_valid_output_are_valid() -> None:
    executor = StubExecutor(stdout=valid_stdout())
    outcome = validate_python_interpreter(ABS_PYTHON, execute=executor)
    assert outcome.is_valid
    assert outcome.python_version == (3, 12, 1)
    assert outcome.distribution_found is True
    assert outcome.package_found is True


def test_nonzero_exit_is_interpreter_invalid() -> None:
    executor = StubExecutor(returncode=1, stdout="", stderr="boom")
    outcome = validate_python_interpreter(ABS_PYTHON, execute=executor)
    assert outcome.code == ErrorCode.INTERPRETER_INVALID


def test_timeout_is_interpreter_invalid_after_exactly_one_attempt() -> None:
    executor = StubExecutor(raises=subprocess.TimeoutExpired(cmd="x", timeout=5.0))
    outcome = validate_python_interpreter(ABS_PYTHON, execute=executor)
    assert outcome.code == ErrorCode.INTERPRETER_INVALID
    assert len(executor.calls) == 1


def test_execution_failure_is_interpreter_invalid_after_one_attempt() -> None:
    executor = StubExecutor(raises=FileNotFoundError("no such executable"))
    outcome = validate_python_interpreter(ABS_PYTHON, execute=executor)
    assert outcome.code == ErrorCode.INTERPRETER_INVALID
    assert len(executor.calls) == 1


def test_unexpected_exception_fails_closed_without_new_error_codes() -> None:
    executor = StubExecutor(raises=RuntimeError("unexpected"))
    outcome = validate_python_interpreter(ABS_PYTHON, execute=executor)
    assert outcome.code == ErrorCode.INTERPRETER_INVALID


@pytest.mark.parametrize(
    "stdout",
    [
        "{not json",
        "",
        json.dumps([3, 12, 0]),
        json.dumps({"python_version": [3, 12, 0], "distribution_found": True}),
        json.dumps(
            {
                "python_version": [3, 12, 0],
                "distribution_found": True,
                "package_found": True,
                "extra": 1,
            }
        ),
        json.dumps(
            {"python_version": "3.12.0", "distribution_found": True, "package_found": True}
        ),
        json.dumps(
            {"python_version": [3, 12], "distribution_found": True, "package_found": True}
        ),
        json.dumps(
            {
                "python_version": [3, 12, 0],
                "distribution_found": "yes",
                "package_found": True,
            }
        ),
        json.dumps(
            {"python_version": [3, 12, 0], "distribution_found": True, "package_found": 1}
        ),
    ],
)
def test_malformed_or_schema_violating_output_is_interpreter_invalid(stdout: str) -> None:
    executor = StubExecutor(stdout=stdout)
    outcome = validate_python_interpreter(ABS_PYTHON, execute=executor)
    assert outcome.code == ErrorCode.INTERPRETER_INVALID


def test_python_below_minimum_is_interpreter_invalid() -> None:
    stdout = json.dumps(
        {
            "python_version": [3, 10, 12],
            "distribution_found": True,
            "package_found": True,
        }
    )
    outcome = validate_python_interpreter(ABS_PYTHON, execute=StubExecutor(stdout=stdout))
    assert outcome.code == ErrorCode.INTERPRETER_INVALID


@pytest.mark.parametrize(
    ("distribution_found", "package_found"),
    [(True, False), (False, True), (False, False)],
)
def test_missing_distribution_or_package_is_core_not_installed(
    distribution_found: bool, package_found: bool
) -> None:
    stdout = json.dumps(
        {
            "python_version": [3, 12, 0],
            "distribution_found": distribution_found,
            "package_found": package_found,
        }
    )
    outcome = validate_python_interpreter(ABS_PYTHON, execute=StubExecutor(stdout=stdout))
    assert outcome.code == ErrorCode.CORE_NOT_INSTALLED


@pytest.mark.parametrize("path", ["python3", "./python", "bin/python", ""])
def test_non_absolute_path_is_rejected_before_execution(path: str) -> None:
    executor = StubExecutor(stdout=valid_stdout())
    outcome = validate_python_interpreter(path, execute=executor)
    assert outcome.code == ErrorCode.INTERPRETER_INVALID
    assert executor.calls == []
