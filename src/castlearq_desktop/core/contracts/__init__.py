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

"""Pure, side-effect-free contracts derived from the approved DEC-A…DEC-I policies.

Everything in this package is **WP0 foundations only**: typed error codes and
pure decision helpers that can be exercised without launching Core, performing
I/O, opening a socket, running a subprocess, or creating a Qt worker.

Nothing here implements Core lifecycle management, HTTP transport, readiness
orchestration, interpreter validation, or settings persistence. Those belong to
later work packages.

Deliberately left undecided, because the approved policy does not decide them:

* the version source, representation, parsing rules, and compatibility range
  (DEC-A) — version admission is therefore an explicit *input*, never a value
  this package extracts or parses;
* endpoint ordering and error precedence among simultaneous readiness failures
  (DEC-B/DEC-D);
* the stable error code for premature owned-child death during readiness
  (DEC-B/DEC-D) — reported as *unclassified* rather than given an invented code;
* the exact interpreter diagnostic command text (DEC-I) — not defined, not
  asserted.
"""

from __future__ import annotations

from castlearq_desktop.core.contracts.deadline import (
    GLOBAL_DEADLINE_SECONDS,
    MAX_REQUEST_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    TRANSPORT_RETRIES_ENABLED,
    Clock,
    Deadline,
)
from castlearq_desktop.core.contracts.errors import (
    APPROVED_ERROR_CODES,
    CastleArqError,
    ErrorCode,
    coerce_error_code,
    error_from_exception,
    is_approved_error_code,
)
from castlearq_desktop.core.contracts.interpreter import (
    ISOLATED_MODE_FLAG,
    MAXIMUM_VALIDATION_TIMEOUT_SECONDS,
    MINIMUM_PYTHON_VERSION,
    REQUIRED_OUTPUT_FIELDS,
    VALIDATION_ATTEMPTS,
    InterpreterValidationResult,
    ValidationOutcome,
    classify_validation_result,
    version_is_supported,
)
from castlearq_desktop.core.contracts.port_conflict import (
    CORE_HOST,
    CORE_PORT,
    PortConflictAssessment,
    StartupEvidence,
    TcpProbeEvidence,
    classify_startup_failure,
)
from castlearq_desktop.core.contracts.readiness import (
    ARTIFACTS_PATH,
    HEALTH_PATH,
    HTTP_SUCCESS_MAX,
    HTTP_SUCCESS_MIN,
    MODELS_PATH,
    ProbeObservation,
    ReadinessCondition,
    ReadinessFailure,
    ReadinessObservations,
    ReadinessOutcome,
    VersionAdmission,
    evaluate_readiness,
    is_admitted,
    is_http_success,
)
from castlearq_desktop.core.contracts.settings import (
    APPROVED_SETTING_KEYS,
    APPLICATION_NAME,
    FORBIDDEN_SETTING_KEYS,
    ORGANIZATION_DOMAIN,
    ORGANIZATION_NAME,
    PYTHON_INTERPRETER_PATH_KEY,
    is_approved_setting_key,
    qsettings_identity,
)
from castlearq_desktop.core.contracts.shutdown import (
    FORCED_TERMINATION_TIMEOUT_SECONDS,
    GRACEFUL_TERMINATION_TIMEOUT_SECONDS,
    TOTAL_SHUTDOWN_BUDGET_SECONDS,
    UI_THREAD_SYNCHRONOUS_WAIT_PERMITTED,
    WORKER_THREAD_FORCE_KILL_PERMITTED,
    CancellationState,
    ShutdownPhase,
    ShutdownState,
    next_shutdown_action,
    phase_for_elapsed,
    remaining_forced_budget,
    remaining_graceful_budget,
    shutdown_budget_exhausted,
    shutdown_failed,
    should_continue_after_request,
    should_issue_request,
)

__all__ = [
    # WP0-A
    "APPROVED_ERROR_CODES",
    "CastleArqError",
    "ErrorCode",
    "coerce_error_code",
    "error_from_exception",
    "is_approved_error_code",
    # WP0-B
    "ARTIFACTS_PATH",
    "HEALTH_PATH",
    "HTTP_SUCCESS_MAX",
    "HTTP_SUCCESS_MIN",
    "MODELS_PATH",
    "ProbeObservation",
    "ReadinessCondition",
    "ReadinessFailure",
    "ReadinessObservations",
    "ReadinessOutcome",
    "VersionAdmission",
    "evaluate_readiness",
    "is_admitted",
    "is_http_success",
    # WP0-C
    "GLOBAL_DEADLINE_SECONDS",
    "MAX_REQUEST_TIMEOUT_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "TRANSPORT_RETRIES_ENABLED",
    "Clock",
    "Deadline",
    # WP0-D
    "FORCED_TERMINATION_TIMEOUT_SECONDS",
    "GRACEFUL_TERMINATION_TIMEOUT_SECONDS",
    "TOTAL_SHUTDOWN_BUDGET_SECONDS",
    "UI_THREAD_SYNCHRONOUS_WAIT_PERMITTED",
    "WORKER_THREAD_FORCE_KILL_PERMITTED",
    "CancellationState",
    "ShutdownPhase",
    "ShutdownState",
    "next_shutdown_action",
    "phase_for_elapsed",
    "remaining_forced_budget",
    "remaining_graceful_budget",
    "shutdown_budget_exhausted",
    "shutdown_failed",
    "should_continue_after_request",
    "should_issue_request",
    # WP0-E
    "CORE_HOST",
    "CORE_PORT",
    "PortConflictAssessment",
    "StartupEvidence",
    "TcpProbeEvidence",
    "classify_startup_failure",
    # WP0-F
    "ISOLATED_MODE_FLAG",
    "MAXIMUM_VALIDATION_TIMEOUT_SECONDS",
    "MINIMUM_PYTHON_VERSION",
    "REQUIRED_OUTPUT_FIELDS",
    "VALIDATION_ATTEMPTS",
    "InterpreterValidationResult",
    "ValidationOutcome",
    "classify_validation_result",
    "version_is_supported",
    # WP0-G
    "APPROVED_SETTING_KEYS",
    "APPLICATION_NAME",
    "FORBIDDEN_SETTING_KEYS",
    "ORGANIZATION_DOMAIN",
    "ORGANIZATION_NAME",
    "PYTHON_INTERPRETER_PATH_KEY",
    "is_approved_setting_key",
    "qsettings_identity",
]
