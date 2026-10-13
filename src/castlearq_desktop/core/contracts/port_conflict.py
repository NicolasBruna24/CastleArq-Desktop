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

"""WP0-E — Port-conflict evidence classifier (pure).

Implements DEC-G as a pure classifier over *supplied* evidence.

``PORT_CONFLICT`` requires **both**:

1. startup evidence that specifically indicates an address/port conflict, and
2. independent evidence that a bounded TCP connection to the configured
   listener succeeded.

Rules encoded here:

* a listener being accessible on its own is insufficient;
* a failed TCP check does **not** prove the port is free;
* ambiguous startup evidence is never promoted to ``PORT_CONFLICT`` and remains
  ``PROCESS_START_FAILED``.

No socket is opened, no port is scanned, no HTTP request or application payload
is sent, no unrelated service is attached to, and no process is terminated.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from castlearq_desktop.core.contracts.errors import ErrorCode

#: The fixed loopback endpoint (DEC-G). Never scanned, changed, or substituted.
CORE_HOST = "127.0.0.1"
CORE_PORT = 8000


class StartupEvidence(Enum):
    """What the owned-child startup attempt reported.

    Only :attr:`ADDRESS_IN_USE` is specific enough to support a conflict
    classification. Everything else is ambiguous and stays
    ``PROCESS_START_FAILED``.
    """

    #: Startup failed for a reason that specifically indicates an address/port
    #: conflict (for example a bind failure on the configured address).
    ADDRESS_IN_USE = "address_in_use"
    #: Startup failed, but the reason does not specifically indicate a conflict.
    AMBIGUOUS = "ambiguous"
    #: No startup failure evidence is available.
    UNKNOWN = "unknown"
    #: Startup did not fail, so there is nothing to classify.
    NO_FAILURE = "no_failure"


class TcpProbeEvidence(Enum):
    """Outcome of the independent bounded TCP connection check."""

    #: A bounded TCP connection to the configured listener succeeded.
    REACHABLE = "reachable"
    #: The bounded TCP connection failed or timed out. This does **not** prove
    #: the port is free.
    UNREACHABLE = "unreachable"
    #: The check was not performed.
    NOT_PERFORMED = "not_performed"


@dataclass(frozen=True)
class PortConflictAssessment:
    """Result of classifying startup evidence together with a TCP probe."""

    code: ErrorCode
    startup_evidence: StartupEvidence
    tcp_evidence: TcpProbeEvidence
    detail: str

    @property
    def is_port_conflict(self) -> bool:
        """Whether both required evidence sources support a conflict."""
        return self.code is ErrorCode.PORT_CONFLICT

    def technical_context(self) -> Dict[str, Any]:
        """Structured, traceback-free diagnostic payload."""
        return {
            "startup_evidence": self.startup_evidence.value,
            "tcp_evidence": self.tcp_evidence.value,
            "code": self.code.value,
            "host": CORE_HOST,
            "port": CORE_PORT,
        }


def classify_startup_failure(
    startup_evidence: StartupEvidence,
    tcp_evidence: TcpProbeEvidence = TcpProbeEvidence.NOT_PERFORMED,
) -> Optional[PortConflictAssessment]:
    """Classify a startup failure using combined evidence (DEC-G).

    Returns ``None`` when there is no failure to classify (startup succeeded or
    no evidence exists), so that an absent failure is never reported as a
    conflict.
    """
    if startup_evidence in (StartupEvidence.NO_FAILURE, StartupEvidence.UNKNOWN):
        return None

    if startup_evidence is StartupEvidence.ADDRESS_IN_USE:
        if tcp_evidence is TcpProbeEvidence.REACHABLE:
            return PortConflictAssessment(
                code=ErrorCode.PORT_CONFLICT,
                startup_evidence=startup_evidence,
                tcp_evidence=tcp_evidence,
                detail=(
                    "startup failed due to an address conflict and an independent "
                    f"TCP check reached a listener at {CORE_HOST}:{CORE_PORT}"
                ),
            )
        # Address-conflict evidence alone is insufficient: the listener check did
        # not corroborate it, so the failure stays a plain start failure.
        return PortConflictAssessment(
            code=ErrorCode.PROCESS_START_FAILED,
            startup_evidence=startup_evidence,
            tcp_evidence=tcp_evidence,
            detail=(
                "startup failed with address-conflict evidence, but the independent "
                "TCP check did not corroborate an accessible listener"
            ),
        )

    # Ambiguous startup evidence is never promoted, even if a listener is
    # reachable: a listener alone is insufficient.
    return PortConflictAssessment(
        code=ErrorCode.PROCESS_START_FAILED,
        startup_evidence=startup_evidence,
        tcp_evidence=tcp_evidence,
        detail="startup failure evidence is ambiguous and does not indicate a conflict",
    )


__all__ = [
    "CORE_HOST",
    "CORE_PORT",
    "PortConflictAssessment",
    "StartupEvidence",
    "TcpProbeEvidence",
    "classify_startup_failure",
]
