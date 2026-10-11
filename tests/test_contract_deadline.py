"""WP0-C contract tests: monotonic deadline-budget arithmetic.

Uses explicit numeric time values only. No real sleep, transport, or retry.
"""

from __future__ import annotations

import pytest

from castlearq_desktop.core.contracts import (
    GLOBAL_DEADLINE_SECONDS,
    MAX_REQUEST_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    TRANSPORT_RETRIES_ENABLED,
    Deadline,
)
from castlearq_desktop.core.http_client import CoreHttpClient


class FakeClock:
    """A deterministic monotonic clock advanced manually."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_approved_policy_constants() -> None:
    """The DEC-E constants must match the approved values exactly."""
    assert GLOBAL_DEADLINE_SECONDS == 20.0
    assert MAX_REQUEST_TIMEOUT_SECONDS == 4.0
    assert POLL_INTERVAL_SECONDS == 0.25
    assert TRANSPORT_RETRIES_ENABLED is False


def test_initial_budget_is_the_full_global_deadline() -> None:
    deadline = Deadline.starting_now(now=0.0)
    assert deadline.remaining(0.0) == 20.0
    assert deadline.deadline_at == 20.0


def test_partially_consumed_budget() -> None:
    deadline = Deadline.starting_now(now=0.0)
    assert deadline.remaining(5.0) == 15.0
    assert deadline.remaining(19.5) == 0.5


def test_effective_timeout_is_the_max_when_budget_is_large() -> None:
    """With ample budget the per-request timeout is the 4 s maximum."""
    deadline = Deadline.starting_now(now=0.0)
    assert deadline.effective_request_timeout(0.0) == 4.0
    assert deadline.effective_request_timeout(10.0) == 4.0


def test_effective_timeout_is_the_remaining_budget_when_smaller() -> None:
    """When the remaining budget is below 4 s, it becomes the timeout."""
    deadline = Deadline.starting_now(now=0.0)
    assert deadline.effective_request_timeout(18.0) == pytest.approx(2.0)
    assert deadline.effective_request_timeout(19.75) == pytest.approx(0.25)


def test_exactly_exhausted_budget() -> None:
    """At exactly 20 s the budget is exhausted and no request may start."""
    deadline = Deadline.starting_now(now=0.0)
    assert deadline.remaining(20.0) == 0.0
    assert deadline.is_expired(20.0)
    assert not deadline.may_start_request(20.0)
    assert deadline.effective_request_timeout(20.0) == 0.0


def test_expired_budget() -> None:
    """Past the deadline, remaining is clamped at zero and requests are refused."""
    deadline = Deadline.starting_now(now=0.0)
    assert deadline.remaining(25.0) == 0.0
    assert deadline.is_expired(25.0)
    assert not deadline.may_start_request(25.0)
    assert deadline.effective_request_timeout(25.0) == 0.0


def test_repeated_calculations_against_the_same_deadline() -> None:
    """Repeated polling must never reset the deadline."""
    deadline = Deadline.starting_now(now=0.0)
    first = deadline.deadline_at
    for elapsed in (0.25, 0.5, 0.75, 1.0, 5.0, 19.0):
        deadline.effective_request_timeout(elapsed)
        deadline.next_poll_delay(elapsed)
        assert deadline.deadline_at == first
    assert deadline.remaining(5.0) == 15.0


def test_poll_delay_never_exceeds_interval_or_remaining_budget() -> None:
    """Polling cannot overshoot the interval or the global deadline."""
    deadline = Deadline.starting_now(now=0.0)
    assert deadline.next_poll_delay(0.0) == 0.25
    assert deadline.next_poll_delay(19.9) == pytest.approx(0.1)
    assert deadline.next_poll_delay(20.0) == 0.0
    assert deadline.next_poll_delay(30.0) == 0.0


def test_polling_sequence_converges_on_the_deadline() -> None:
    """A 250 ms polling loop reaches exhaustion without exceeding 20 s."""
    clock = FakeClock(start=0.0)
    deadline = Deadline.starting_now(now=clock.monotonic())
    cycles = 0
    while deadline.may_start_request(clock.monotonic()):
        deadline.effective_request_timeout(clock.monotonic())
        clock.advance(deadline.next_poll_delay(clock.monotonic()))
        cycles += 1
        assert cycles <= 100, "polling must terminate within the global deadline"
    assert clock.monotonic() == pytest.approx(20.0)
    assert deadline.is_expired(clock.monotonic())


def test_custom_max_timeout_is_respected() -> None:
    """An explicit lower per-request cap still wins over the remaining budget."""
    deadline = Deadline.starting_now(now=0.0)
    assert deadline.effective_request_timeout(1.0, max_request_timeout=2.0) == 2.0
    assert deadline.effective_request_timeout(1.0, max_request_timeout=0.5) == 0.5


def test_http_client_default_timeout_is_the_approved_maximum() -> None:
    """The inert placeholder default must not contradict DEC-E's 4 s cap."""
    client = CoreHttpClient()
    assert client._timeout == MAX_REQUEST_TIMEOUT_SECONDS == 4.0


def test_http_client_still_makes_no_requests() -> None:
    """The timeout correction changes no behaviour: still no network activity."""
    client = CoreHttpClient(base_url="http://127.0.0.1:8000")
    assert client.base_url == "http://127.0.0.1:8000"
    with pytest.raises(NotImplementedError):
        client.get("/health")
