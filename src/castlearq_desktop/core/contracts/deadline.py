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

"""WP0-C — Monotonic deadline-budget arithmetic (pure).

Implements DEC-E: a 20 s global readiness deadline measured by a monotonic
clock, a 4 s maximum per-request timeout, a 250 ms polling interval, and an
effective per-request timeout of ``min(4 s, remaining budget)``.

This module contains no sleeps, no transport, and no retries. It only performs
arithmetic over explicitly supplied or injected time values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

#: Global readiness deadline, in seconds (DEC-E).
GLOBAL_DEADLINE_SECONDS = 20.0

#: Maximum timeout for any single HTTP request, in seconds (DEC-E).
MAX_REQUEST_TIMEOUT_SECONDS = 4.0

#: Polling interval, in seconds (DEC-E: 250 ms).
POLL_INTERVAL_SECONDS = 0.25

#: Automatic transport retries are disabled (DEC-E). Retained as an explicit,
#: assertable constant rather than an implicit default.
TRANSPORT_RETRIES_ENABLED = False


class Clock(Protocol):
    """Minimal monotonic clock seam, injectable for tests."""

    def monotonic(self) -> float:
        """Return a monotonically non-decreasing time value in seconds."""
        ...


@dataclass(frozen=True)
class Deadline:
    """An immutable monotonic deadline with derived budget queries.

    Constructed once from a clock reading; repeated polling never resets it.
    """

    deadline_at: float

    @classmethod
    def starting_now(
        cls,
        now: float,
        total_seconds: float = GLOBAL_DEADLINE_SECONDS,
    ) -> "Deadline":
        """Create a deadline *total_seconds* after the supplied time *now*."""
        return cls(deadline_at=now + float(total_seconds))

    def remaining(self, now: float) -> float:
        """Return the remaining budget in seconds; never negative."""
        return max(0.0, self.deadline_at - now)

    def is_expired(self, now: float) -> bool:
        """Whether the global budget is exhausted at time *now*."""
        return self.remaining(now) <= 0.0

    def may_start_request(self, now: float) -> bool:
        """Whether a new request is permitted at time *now* (DEC-E)."""
        return not self.is_expired(now)

    def effective_request_timeout(
        self,
        now: float,
        max_request_timeout: float = MAX_REQUEST_TIMEOUT_SECONDS,
    ) -> float:
        """Return ``min(max_request_timeout, remaining budget)`` (DEC-E).

        Callers must gate on :meth:`may_start_request` first: when the budget is
        exhausted this returns ``0.0``, which is a sentinel meaning "do not
        start a request", not a usable timeout.
        """
        return min(float(max_request_timeout), self.remaining(now))

    def next_poll_delay(
        self,
        now: float,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> float:
        """Return the delay before the next polling cycle.

        Never exceeds the configured interval and never exceeds the remaining
        budget, so polling cannot overshoot the global deadline.
        """
        if self.is_expired(now):
            return 0.0
        return min(float(poll_interval), self.remaining(now))


__all__ = [
    "Clock",
    "Deadline",
    "GLOBAL_DEADLINE_SECONDS",
    "MAX_REQUEST_TIMEOUT_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "TRANSPORT_RETRIES_ENABLED",
]
