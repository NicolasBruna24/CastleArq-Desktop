"""WP0-E contract tests: port-conflict evidence classifier.

All evidence is supplied as plain values. No socket is opened and no port,
process, or service is touched.
"""

from __future__ import annotations

import pytest

from castlearq_desktop.core.contracts import (
    CORE_HOST,
    CORE_PORT,
    StartupEvidence,
    TcpProbeEvidence,
    classify_startup_failure,
)
from castlearq_desktop.core.contracts.errors import ErrorCode


def test_fixed_endpoint_is_the_approved_address() -> None:
    """The configured address must remain the fixed loopback endpoint."""
    assert CORE_HOST == "127.0.0.1"
    assert CORE_PORT == 8000


def test_both_evidence_sources_required_for_port_conflict() -> None:
    """Address-conflict evidence plus an accessible listener is a conflict."""
    assessment = classify_startup_failure(
        StartupEvidence.ADDRESS_IN_USE,
        TcpProbeEvidence.REACHABLE,
    )
    assert assessment is not None
    assert assessment.code is ErrorCode.PORT_CONFLICT
    assert assessment.is_port_conflict


@pytest.mark.parametrize(
    "tcp_evidence",
    [TcpProbeEvidence.UNREACHABLE, TcpProbeEvidence.NOT_PERFORMED],
)
def test_address_conflict_without_corroboration_is_not_a_conflict(
    tcp_evidence: TcpProbeEvidence,
) -> None:
    """Startup evidence alone is insufficient for PORT_CONFLICT."""
    assessment = classify_startup_failure(
        StartupEvidence.ADDRESS_IN_USE,
        tcp_evidence,
    )
    assert assessment is not None
    assert assessment.code is ErrorCode.PROCESS_START_FAILED
    assert not assessment.is_port_conflict


@pytest.mark.parametrize(
    "tcp_evidence",
    [TcpProbeEvidence.REACHABLE, TcpProbeEvidence.UNREACHABLE,
     TcpProbeEvidence.NOT_PERFORMED],
)
def test_listener_alone_is_insufficient(tcp_evidence: TcpProbeEvidence) -> None:
    """An accessible listener never promotes ambiguous evidence to a conflict."""
    assessment = classify_startup_failure(
        StartupEvidence.AMBIGUOUS,
        tcp_evidence,
    )
    assert assessment is not None
    assert assessment.code is ErrorCode.PROCESS_START_FAILED
    assert not assessment.is_port_conflict


def test_default_tcp_evidence_is_not_performed() -> None:
    """Omitting the probe must not manufacture corroboration."""
    assessment = classify_startup_failure(StartupEvidence.ADDRESS_IN_USE)
    assert assessment is not None
    assert assessment.code is ErrorCode.PROCESS_START_FAILED
    assert assessment.tcp_evidence is TcpProbeEvidence.NOT_PERFORMED


@pytest.mark.parametrize(
    "startup_evidence",
    [StartupEvidence.UNKNOWN, StartupEvidence.NO_FAILURE],
)
def test_no_failure_means_no_classification(startup_evidence: StartupEvidence) -> None:
    """Without a startup failure there is nothing to classify."""
    assert classify_startup_failure(startup_evidence, TcpProbeEvidence.REACHABLE) is None


def test_failed_tcp_check_does_not_prove_the_port_is_free() -> None:
    """An unreachable listener must never be read as 'port is free'."""
    assessment = classify_startup_failure(
        StartupEvidence.AMBIGUOUS,
        TcpProbeEvidence.UNREACHABLE,
    )
    assert assessment is not None
    assert assessment.code is ErrorCode.PROCESS_START_FAILED
    assert "free" not in assessment.detail.lower()


def test_diagnostic_context_records_both_evidence_sources() -> None:
    """Diagnostics must show which evidence sources were combined."""
    assessment = classify_startup_failure(
        StartupEvidence.ADDRESS_IN_USE,
        TcpProbeEvidence.REACHABLE,
    )
    context = assessment.technical_context()
    assert context["startup_evidence"] == "address_in_use"
    assert context["tcp_evidence"] == "reachable"
    assert context["host"] == "127.0.0.1"
    assert context["port"] == 8000
    assert context["code"] == "PORT_CONFLICT"
    assert "Traceback" not in repr(context)


def test_full_evidence_matrix_is_exhaustive() -> None:
    """Every evidence combination yields exactly one approved outcome."""
    for startup_evidence in StartupEvidence:
        for tcp_evidence in TcpProbeEvidence:
            assessment = classify_startup_failure(startup_evidence, tcp_evidence)
            if assessment is None:
                assert startup_evidence in (
                    StartupEvidence.UNKNOWN,
                    StartupEvidence.NO_FAILURE,
                )
                continue
            assert assessment.code in (
                ErrorCode.PORT_CONFLICT,
                ErrorCode.PROCESS_START_FAILED,
            )
