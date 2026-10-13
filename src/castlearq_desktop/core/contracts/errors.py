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

"""WP0-A — Stable, typed error contracts.

This module defines *only* the approved stable error codes (DEC-D) and a typed
error container that keeps three concerns separate:

* a stable, machine-readable ``code``;
* an actionable, user-facing message;
* structured technical context suitable for diagnostics.

Deliberately **not** provided here: any mapping from a runtime failure
condition to a code. Conditions whose mapping is still undecided (for example
premature owned-child death during readiness) must remain unclassified rather
than be given an invented code.

No stack trace and no raw ``stderr`` is ever promoted to the primary
user-facing message.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional


class ErrorCode(str, Enum):
    """The nine approved stable error codes (DEC-D).

    Each member's value is identical to its name so that the code is stable in
    logs, in structured diagnostics, and across serialization boundaries.
    """

    INTERPRETER_INVALID = "INTERPRETER_INVALID"
    CORE_NOT_INSTALLED = "CORE_NOT_INSTALLED"
    PROCESS_START_FAILED = "PROCESS_START_FAILED"
    PORT_CONFLICT = "PORT_CONFLICT"
    READINESS_TIMEOUT = "READINESS_TIMEOUT"
    HEALTH_CHECK_FAILED = "HEALTH_CHECK_FAILED"
    API_CONTRACT_INCOMPATIBLE = "API_CONTRACT_INCOMPATIBLE"
    API_PROBE_FAILED = "API_PROBE_FAILED"
    SHUTDOWN_FAILED = "SHUTDOWN_FAILED"


#: The complete approved set of stable error codes.
APPROVED_ERROR_CODES = frozenset(ErrorCode)

#: Markers used to refuse a stack trace as a primary user-facing message.
_TRACEBACK_MARKERS = (
    "Traceback (most recent call last)",
    'File "',
)


def is_approved_error_code(code: object) -> bool:
    """Return ``True`` when *code* is one of the approved stable codes."""
    if isinstance(code, ErrorCode):
        return code in APPROVED_ERROR_CODES
    try:
        ErrorCode(code)
    except ValueError:
        return False
    return True


def coerce_error_code(code: "str | ErrorCode") -> ErrorCode:
    """Return *code* as an :class:`ErrorCode`, rejecting unapproved values.

    Unapproved codes are refused loudly so that an invented code can never be
    introduced silently.
    """
    if isinstance(code, ErrorCode):
        if code not in APPROVED_ERROR_CODES:
            raise ValueError(f"Unapproved error code: {code!r}")
        return code
    try:
        return ErrorCode(code)
    except ValueError as exc:
        raise ValueError(
            f"Unapproved error code {code!r}; approved codes are "
            f"{sorted(c.value for c in APPROVED_ERROR_CODES)}"
        ) from exc


def _reject_traceback_message(message: str) -> str:
    """Refuse a message that looks like a stack trace or raw stderr dump."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("A user-facing message must be a non-empty string.")
    for marker in _TRACEBACK_MARKERS:
        if marker in message:
            raise ValueError(
                "A stack trace or raw stderr must not be used as the primary "
                "user-facing message; put it in the technical context instead."
            )
    return message


class CastleArqError(Exception):
    """Typed Desktop error: stable code + actionable message + diagnostics.

    ``str(error)`` is always the actionable user-facing message. Technical
    detail lives exclusively in :attr:`technical_context` and
    :meth:`diagnostics`, which never contain a formatted traceback.
    """

    def __init__(
        self,
        code: "str | ErrorCode",
        user_message: str,
        technical_context: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.code: ErrorCode = coerce_error_code(code)
        self.user_message: str = _reject_traceback_message(user_message)
        context: Dict[str, Any] = dict(technical_context or {})
        for key, value in context.items():
            if isinstance(value, str) and any(m in value for m in _TRACEBACK_MARKERS):
                raise ValueError(
                    "Technical context must not embed a stack trace; record "
                    f"structured facts instead (offending key: {key!r})."
                )
        self.technical_context: Mapping[str, Any] = MappingProxyType(context)
        super().__init__(self.user_message)

    def diagnostics(self) -> Dict[str, Any]:
        """Return a structured, traceback-free diagnostic payload."""
        payload: Dict[str, Any] = {
            "code": self.code.value,
            "technical_context": dict(self.technical_context),
        }
        cause = self.__cause__
        if cause is not None:
            # Only the cause's type and message are recorded — never its traceback.
            payload["cause_type"] = type(cause).__name__
            payload["cause_message"] = str(cause)
        return payload

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"{type(self).__name__}(code={self.code.value!r}, "
            f"user_message={self.user_message!r})"
        )


def error_from_exception(
    code: "str | ErrorCode",
    user_message: str,
    cause: BaseException,
    **technical_context: Any,
) -> CastleArqError:
    """Build a :class:`CastleArqError` from a caught exception.

    The originating exception's type and message are preserved as structured
    technical context; its traceback is deliberately discarded so it can never
    surface as a primary message or as an unbounded diagnostic blob.
    """
    payload = dict(technical_context)
    payload["cause_type"] = type(cause).__name__
    payload["cause_message"] = str(cause)
    error = CastleArqError(code, user_message, payload)
    # Chain the cause so ``raise error`` renders normal Python traceback
    # chaining, without the cause ever entering the user-facing message.
    error.__cause__ = cause
    return error


__all__ = [
    "APPROVED_ERROR_CODES",
    "CastleArqError",
    "ErrorCode",
    "coerce_error_code",
    "error_from_exception",
    "is_approved_error_code",
]
