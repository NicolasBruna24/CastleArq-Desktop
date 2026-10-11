"""WP0-A contract tests: stable error codes and the typed error container.

Pure unit tests. No I/O, no Core, no Qt.
"""

from __future__ import annotations

import pytest

from castlearq_desktop.core.contracts import (
    APPROVED_ERROR_CODES,
    CastleArqError,
    ErrorCode,
    coerce_error_code,
    error_from_exception,
    is_approved_error_code,
)

APPROVED_CODE_VALUES = [
    "INTERPRETER_INVALID",
    "CORE_NOT_INSTALLED",
    "PROCESS_START_FAILED",
    "PORT_CONFLICT",
    "READINESS_TIMEOUT",
    "HEALTH_CHECK_FAILED",
    "API_CONTRACT_INCOMPATIBLE",
    "API_PROBE_FAILED",
    "SHUTDOWN_FAILED",
]


@pytest.mark.parametrize("value", APPROVED_CODE_VALUES)
def test_every_approved_code_exists(value: str) -> None:
    """Each approved identifier must be present as an ErrorCode member."""
    assert ErrorCode(value).value == value


def test_approved_code_set_is_exactly_the_approved_nine() -> None:
    """No extra and no missing codes may exist."""
    assert {c.value for c in ErrorCode} == set(APPROVED_CODE_VALUES)
    assert len(APPROVED_CODE_VALUES) == 9
    assert len(ErrorCode) == 9


def test_code_values_equal_member_names() -> None:
    """Codes must be stable: value identical to name for log/JSON stability."""
    for code in ErrorCode:
        assert code.value == code.name


@pytest.mark.parametrize("value", APPROVED_CODE_VALUES)
def test_approved_code_recognition(value: str) -> None:
    """Approved codes are recognised both as strings and as members."""
    assert is_approved_error_code(value)
    assert is_approved_error_code(ErrorCode(value))


@pytest.mark.parametrize(
    "value",
    ["NOT_A_CODE", "", "port_conflict", "READINESS", "CORE_INVALID"],
)
def test_unapproved_codes_are_rejected(value: str) -> None:
    """An invented code must never be accepted silently."""
    assert not is_approved_error_code(value)
    with pytest.raises(ValueError):
        coerce_error_code(value)


def test_typed_error_exposes_code_message_and_context() -> None:
    """The three concerns must be separately accessible."""
    error = CastleArqError(
        ErrorCode.PORT_CONFLICT,
        "Port 8000 is already in use. Stop the other service, then retry.",
        {"host": "127.0.0.1", "port": 8000},
    )
    assert error.code is ErrorCode.PORT_CONFLICT
    assert error.user_message == (
        "Port 8000 is already in use. Stop the other service, then retry."
    )
    assert error.technical_context["port"] == 8000


def test_primary_message_is_the_actionable_user_message() -> None:
    """``str(error)`` must be the user message, never diagnostics."""
    error = CastleArqError(
        ErrorCode.READINESS_TIMEOUT,
        "Core did not become ready within 20 seconds.",
        {"elapsed_seconds": 20.0},
    )
    assert str(error) == "Core did not become ready within 20 seconds."
    assert "elapsed_seconds" not in str(error)


def test_diagnostics_are_separate_from_user_message() -> None:
    """Technical detail lives only in diagnostics(), not in the message."""
    error = CastleArqError(
        ErrorCode.HEALTH_CHECK_FAILED,
        "Core did not answer the health check.",
        {"status": 503, "path": "/health"},
    )
    diagnostics = error.diagnostics()
    assert diagnostics["code"] == "HEALTH_CHECK_FAILED"
    assert diagnostics["technical_context"]["status"] == 503
    assert "503" not in error.user_message


def test_technical_context_is_immutable() -> None:
    """Diagnostic context must not be mutable through the public mapping."""
    error = CastleArqError(ErrorCode.API_PROBE_FAILED, "Probe failed.", {"k": "v"})
    with pytest.raises(TypeError):
        error.technical_context["k"] = "other"  # type: ignore[index]


def test_stack_trace_is_refused_as_primary_message() -> None:
    """A stack trace must never become the user-facing message."""
    trace = 'Traceback (most recent call last):\n  File "x.py", line 1'
    with pytest.raises(ValueError):
        CastleArqError(ErrorCode.PROCESS_START_FAILED, trace)


def test_raw_stderr_is_refused_as_primary_message() -> None:
    """Raw stderr content must never become the user-facing message."""
    with pytest.raises(ValueError):
        CastleArqError(ErrorCode.PROCESS_START_FAILED, 'File "/tmp/x.py", line 3')


def test_stack_trace_is_refused_in_technical_context() -> None:
    """Diagnostic context must not embed an unbounded traceback either."""
    with pytest.raises(ValueError):
        CastleArqError(
            ErrorCode.PROCESS_START_FAILED,
            "Core could not be started.",
            {"trace": 'Traceback (most recent call last):\n  File "x.py"'},
        )


def test_empty_user_message_is_refused() -> None:
    """A user message must be actionable, i.e. non-empty."""
    with pytest.raises(ValueError):
        CastleArqError(ErrorCode.PROCESS_START_FAILED, "   ")


def test_error_from_exception_preserves_cause_without_traceback() -> None:
    """Cause type/message are recorded; no traceback is stored or rendered."""
    try:
        raise OSError(98, "Address already in use")
    except OSError as exc:
        error = error_from_exception(
            ErrorCode.PORT_CONFLICT,
            "Port 8000 is already in use.",
            exc,
            errno=98,
        )
    assert error.code is ErrorCode.PORT_CONFLICT
    diagnostics = error.diagnostics()
    assert diagnostics["cause_type"] == "OSError"
    assert "Address already in use" in diagnostics["cause_message"]
    assert "Traceback" not in repr(diagnostics)
    assert "Traceback" not in str(error)


def test_error_is_raisable_and_catchable_by_code() -> None:
    """Typed errors must be usable in normal exception handling."""
    with pytest.raises(CastleArqError) as excinfo:
        raise CastleArqError(ErrorCode.SHUTDOWN_FAILED, "Core did not shut down.")
    assert excinfo.value.code is ErrorCode.SHUTDOWN_FAILED
