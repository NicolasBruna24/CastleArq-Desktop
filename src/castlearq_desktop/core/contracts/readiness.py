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

"""WP0-B — API-contract admission and readiness predicates (pure).

Implements DEC-A (fail-closed contract admission) and DEC-B (the readiness
condition set) as **pure decision logic over explicitly supplied observations**.

This module never performs a request, polls, sleeps, inspects a real process,
or starts a service.

Deliberately **not** decided here, because the approved policy does not decide
it either:

* the version source, field name, representation, syntax, or parser;
* the compatibility range and how unknown/future versions are judged;
* endpoint ordering and error precedence when several conditions fail;
* the stable error code for premature owned-child death during readiness.

Accordingly, version admission is modelled as an **explicit input
classification**, never as an extracted or parsed version value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

from castlearq_desktop.core.contracts.errors import ErrorCode

#: Endpoint paths fixed by DEC-B. No other path is asserted anywhere.
HEALTH_PATH = "/health"
MODELS_PATH = "/v1/models"
ARTIFACTS_PATH = "/v1/artifacts"

#: Inclusive bounds of the HTTP success range (DEC-B: success means 2xx).
HTTP_SUCCESS_MIN = 200
HTTP_SUCCESS_MAX = 299


class VersionAdmission(Enum):
    """Outcome of API-contract admission, supplied as an explicit input.

    Only :attr:`ADMITTED` may ever establish readiness. Every other member
    fails closed.

    ``UNKNOWN`` and ``FUTURE`` are kept distinct purely so diagnostics can be
    precise; both are treated identically — neither is admitted.
    """

    ADMITTED = "admitted"
    MISSING = "missing"
    MALFORMED = "malformed"
    UNKNOWN = "unknown"
    FUTURE = "future"
    INCOMPATIBLE = "incompatible"
    #: Admission was never evaluated at all.
    NOT_EVALUATED = "not_evaluated"


def is_admitted(admission: VersionAdmission) -> bool:
    """Return ``True`` only for an explicitly admitted contract."""
    return admission is VersionAdmission.ADMITTED


class ReadinessCondition(Enum):
    """The individual conditions whose conjunction is readiness (DEC-B)."""

    CHILD_ALIVE_BEFORE = "child_alive_before"
    HEALTH = "health"
    MODELS = "models"
    ARTIFACTS = "artifacts"
    CHILD_ALIVE_AFTER = "child_alive_after"
    API_CONTRACT = "api_contract"


@dataclass(frozen=True)
class ProbeObservation:
    """A single HTTP probe outcome.

    ``status`` is ``None`` when the probe was never observed. A missing
    observation is *not* a success and is *not* silently classified.
    """

    path: str
    status: Optional[int] = None

    @property
    def observed(self) -> bool:
        """Whether an outcome was actually observed."""
        return self.status is not None

    def is_success(self) -> bool:
        """Whether the observed status is in the 2xx range."""
        return self.observed and HTTP_SUCCESS_MIN <= int(self.status) <= HTTP_SUCCESS_MAX


def is_http_success(status: Optional[int]) -> bool:
    """Return ``True`` only for an observed 2xx status (DEC-B)."""
    if status is None:
        return False
    return HTTP_SUCCESS_MIN <= int(status) <= HTTP_SUCCESS_MAX


@dataclass(frozen=True)
class ReadinessFailure:
    """One unsatisfied readiness condition.

    ``error_code`` is ``None`` when the approved policy assigns no stable code
    to that condition; the failure is then reported *unclassified* rather than
    being given an invented code.
    """

    condition: ReadinessCondition
    detail: str
    error_code: Optional[ErrorCode] = None
    technical_context: Mapping[str, Any] = field(default_factory=dict)

    @property
    def classified(self) -> bool:
        """Whether an approved stable code exists for this failure."""
        return self.error_code is not None


@dataclass(frozen=True)
class ReadinessObservations:
    """Everything the readiness predicate is allowed to look at.

    Every field defaults to "not observed"/"not admitted" so a partially
    populated observation set can never be mistaken for a ready one.
    """

    child_alive_before: bool = False
    health: Optional[ProbeObservation] = None
    models: Optional[ProbeObservation] = None
    artifacts: Optional[ProbeObservation] = None
    child_alive_after: bool = False
    api_contract: VersionAdmission = VersionAdmission.NOT_EVALUATED


@dataclass(frozen=True)
class ReadinessOutcome:
    """Result of the readiness predicate.

    All unsatisfied conditions are reported together. This module does **not**
    choose an ordering or precedence among them, because none is approved.
    """

    failures: Tuple[ReadinessFailure, ...] = ()

    @property
    def ready(self) -> bool:
        """Readiness holds only when no condition is unsatisfied."""
        return not self.failures

    @property
    def unclassified_failures(self) -> Tuple[ReadinessFailure, ...]:
        """Failures for which no approved stable code exists."""
        return tuple(f for f in self.failures if not f.classified)

    def error_code(self) -> Optional[ErrorCode]:
        """Return the single applicable code, or ``None`` when ambiguous.

        ``None`` means either "ready" or "more than one condition failed and no
        precedence is approved". Callers must not read ``None`` as success —
        use :attr:`ready` for that.
        """
        if len(self.failures) != 1:
            return None
        return self.failures[0].error_code

    def technical_context(self) -> Dict[str, Any]:
        """Structured, traceback-free diagnostic summary."""
        return {
            "ready": self.ready,
            "failures": [
                {
                    "condition": f.condition.value,
                    "detail": f.detail,
                    "code": f.error_code.value if f.error_code else None,
                    "context": dict(f.technical_context),
                }
                for f in self.failures
            ],
        }


def _probe_failure(
    condition: ReadinessCondition,
    observation: Optional[ProbeObservation],
    path: str,
    error_code: ErrorCode,
) -> Optional[ReadinessFailure]:
    """Return a failure for *observation*, or ``None`` when it succeeded.

    A missing observation fails closed with **no** code assigned: the approved
    policy assigns stage codes to endpoint *failures*, not to absent
    observations.
    """
    if observation is None:
        return ReadinessFailure(
            condition=condition,
            detail=f"no observation supplied for {path}",
            error_code=None,
            technical_context={"path": path, "observed": False},
        )
    if observation.is_success():
        return None
    return ReadinessFailure(
        condition=condition,
        detail=f"{path} did not return a 2xx status",
        error_code=error_code,
        technical_context={"path": observation.path, "status": observation.status},
    )


def evaluate_readiness(observations: ReadinessObservations) -> ReadinessOutcome:
    """Evaluate DEC-B readiness over supplied observations; pure, no side effects.

    Readiness requires **all** of:

    1. the owned child process alive *before* validation;
    2. ``GET /health`` observed with a 2xx status;
    3. ``GET /v1/models`` observed with a 2xx status;
    4. ``GET /v1/artifacts`` observed with a 2xx status;
    5. the owned child process alive *again* before declaring readiness;
    6. an explicitly admitted API contract.

    A partial result never means ready.
    """
    failures: List[ReadinessFailure] = []

    if not observations.child_alive_before:
        failures.append(
            ReadinessFailure(
                condition=ReadinessCondition.CHILD_ALIVE_BEFORE,
                detail="owned child process was not alive before readiness validation",
                # No approved code exists for premature child death.
                error_code=None,
                technical_context={"alive": False, "phase": "before"},
            )
        )

    for condition, observation, path, code in (
        (ReadinessCondition.HEALTH, observations.health, HEALTH_PATH,
         ErrorCode.HEALTH_CHECK_FAILED),
        (ReadinessCondition.MODELS, observations.models, MODELS_PATH,
         ErrorCode.API_PROBE_FAILED),
        (ReadinessCondition.ARTIFACTS, observations.artifacts, ARTIFACTS_PATH,
         ErrorCode.API_PROBE_FAILED),
    ):
        failure = _probe_failure(condition, observation, path, code)
        if failure is not None:
            failures.append(failure)

    if not observations.child_alive_after:
        failures.append(
            ReadinessFailure(
                condition=ReadinessCondition.CHILD_ALIVE_AFTER,
                detail="owned child process was not alive when readiness was declared",
                # No approved code exists for premature child death.
                error_code=None,
                technical_context={"alive": False, "phase": "after"},
            )
        )

    if not is_admitted(observations.api_contract):
        failures.append(
            ReadinessFailure(
                condition=ReadinessCondition.API_CONTRACT,
                detail="API-contract admission did not succeed",
                error_code=ErrorCode.API_CONTRACT_INCOMPATIBLE,
                technical_context={
                    "admission": observations.api_contract.value,
                    "admitted": False,
                },
            )
        )

    return ReadinessOutcome(failures=tuple(failures))


__all__ = [
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
]
