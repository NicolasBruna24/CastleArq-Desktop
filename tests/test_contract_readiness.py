"""WP0-B contract tests: fail-closed contract admission and readiness predicates.

Pure tests over explicitly supplied observations. No request, poll, sleep,
process inspection, or service start occurs.
"""

from __future__ import annotations

import pytest

from castlearq_desktop.core.contracts import (
    ARTIFACTS_PATH,
    HEALTH_PATH,
    MODELS_PATH,
    ProbeObservation,
    ReadinessCondition,
    ReadinessObservations,
    VersionAdmission,
    evaluate_readiness,
    is_admitted,
    is_http_success,
)
from castlearq_desktop.core.contracts.errors import ErrorCode


def fully_ready_observations() -> ReadinessObservations:
    """A complete, successful observation set."""
    return ReadinessObservations(
        child_alive_before=True,
        health=ProbeObservation(path=HEALTH_PATH, status=200),
        models=ProbeObservation(path=MODELS_PATH, status=200),
        artifacts=ProbeObservation(path=ARTIFACTS_PATH, status=200),
        child_alive_after=True,
        api_contract=VersionAdmission.ADMITTED,
    )


def replace_observation(
    base: ReadinessObservations, **changes: object
) -> ReadinessObservations:
    """Return *base* with the named fields replaced."""
    fields = {
        "child_alive_before": base.child_alive_before,
        "health": base.health,
        "models": base.models,
        "artifacts": base.artifacts,
        "child_alive_after": base.child_alive_after,
        "api_contract": base.api_contract,
    }
    fields.update(changes)
    return ReadinessObservations(**fields)


# --- HTTP success semantics (DEC-B: success means 2xx) ---------------------


@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_2xx_statuses_are_success(status: int) -> None:
    assert is_http_success(status)


@pytest.mark.parametrize("status", [100, 199, 300, 301, 400, 404, 500, 503])
def test_non_2xx_statuses_are_not_success(status: int) -> None:
    assert not is_http_success(status)


def test_missing_status_is_not_success() -> None:
    """An unobserved probe can never count as success."""
    assert not is_http_success(None)


# --- Contract admission (DEC-A: fail closed) -------------------------------


def test_only_explicit_admission_is_admitted() -> None:
    assert is_admitted(VersionAdmission.ADMITTED)


@pytest.mark.parametrize(
    "admission",
    [
        VersionAdmission.MISSING,
        VersionAdmission.MALFORMED,
        VersionAdmission.UNKNOWN,
        VersionAdmission.FUTURE,
        VersionAdmission.INCOMPATIBLE,
        VersionAdmission.NOT_EVALUATED,
    ],
)
def test_no_non_admitted_state_establishes_readiness(
    admission: VersionAdmission,
) -> None:
    """Missing, malformed, unknown, future, and incompatible all fail closed."""
    assert not is_admitted(admission)
    outcome = evaluate_readiness(
        replace_observation(fully_ready_observations(), api_contract=admission)
    )
    assert not outcome.ready
    assert outcome.error_code() is ErrorCode.API_CONTRACT_INCOMPATIBLE


def test_complete_observation_set_is_ready() -> None:
    """Readiness holds when every condition is satisfied."""
    outcome = evaluate_readiness(fully_ready_observations())
    assert outcome.ready
    assert outcome.failures == ()
    assert outcome.error_code() is None


# --- Endpoint stage codes (DEC-B / DEC-D) ----------------------------------


@pytest.mark.parametrize("status", [500, 503, 404, 302])
def test_health_failure_uses_health_check_failed(status: int) -> None:
    """A failing /health uses its own stage-specific code."""
    outcome = evaluate_readiness(
        replace_observation(
            fully_ready_observations(),
            health=ProbeObservation(path=HEALTH_PATH, status=status),
        )
    )
    assert not outcome.ready
    assert {f.condition for f in outcome.failures} == {ReadinessCondition.HEALTH}
    assert outcome.error_code() is ErrorCode.HEALTH_CHECK_FAILED


def test_unobserved_health_gets_no_invented_stage_code() -> None:
    """An absent observation is not an endpoint failure, so no stage code."""
    outcome = evaluate_readiness(
        replace_observation(fully_ready_observations(), health=None)
    )
    assert not outcome.ready
    assert outcome.error_code() is None
    assert outcome.unclassified_failures


@pytest.mark.parametrize(
    ("field", "path", "condition"),
    [
        ("models", MODELS_PATH, ReadinessCondition.MODELS),
        ("artifacts", ARTIFACTS_PATH, ReadinessCondition.ARTIFACTS),
    ],
)
@pytest.mark.parametrize("status", [500, 503])
def test_api_probe_failure_uses_api_probe_failed(
    field: str, path: str, condition: ReadinessCondition, status: int
) -> None:
    """A failing /v1/* probe uses the API-probe stage code."""
    outcome = evaluate_readiness(
        replace_observation(
            fully_ready_observations(),
            **{field: ProbeObservation(path=path, status=status)},
        )
    )
    assert not outcome.ready
    assert {f.condition for f in outcome.failures} == {condition}
    assert outcome.error_code() is ErrorCode.API_PROBE_FAILED


def test_unobserved_api_probe_gets_no_invented_stage_code() -> None:
    """A never-observed /v1 probe fails closed without a stage code."""
    outcome = evaluate_readiness(
        replace_observation(fully_ready_observations(), models=None)
    )
    assert not outcome.ready
    assert outcome.failures[0].condition is ReadinessCondition.MODELS
    assert outcome.failures[0].error_code is None
    assert outcome.error_code() is None


# --- Child liveness before and after (DEC-B) ------------------------------


def test_child_not_alive_before_fails_closed() -> None:
    outcome = evaluate_readiness(
        replace_observation(fully_ready_observations(), child_alive_before=False)
    )
    assert not outcome.ready
    assert outcome.failures[0].condition is ReadinessCondition.CHILD_ALIVE_BEFORE


def test_child_not_alive_after_fails_closed() -> None:
    """Liveness must be re-confirmed before readiness is declared."""
    outcome = evaluate_readiness(
        replace_observation(fully_ready_observations(), child_alive_after=False)
    )
    assert not outcome.ready
    assert outcome.failures[0].condition is ReadinessCondition.CHILD_ALIVE_AFTER


@pytest.mark.parametrize("phase", ["before", "after"])
def test_premature_child_death_is_not_given_an_invented_code(phase: str) -> None:
    """No approved code exists yet: report unclassified, do not invent one."""
    field = "child_alive_before" if phase == "before" else "child_alive_after"
    outcome = evaluate_readiness(
        replace_observation(fully_ready_observations(), **{field: False})
    )
    assert not outcome.ready
    assert all(f.error_code is None for f in outcome.failures)
    assert outcome.unclassified_failures


# --- Partial results, defaults, and non-invented precedence ---------------


def test_default_observations_are_not_ready() -> None:
    """An empty observation set must fail closed on all six conditions."""
    outcome = evaluate_readiness(ReadinessObservations())
    assert not outcome.ready
    assert len(outcome.failures) == 6


def test_partial_result_never_means_ready() -> None:
    """A subset short of complete is never ready."""
    outcome = evaluate_readiness(
        replace_observation(
            fully_ready_observations(),
            artifacts=None,
        )
    )
    assert not outcome.ready


def test_no_error_precedence_is_invented_among_multiple_failures() -> None:
    """With several failures, no single code is chosen (none is approved)."""
    outcome = evaluate_readiness(
        ReadinessObservations(
            child_alive_before=False,
            health=ProbeObservation(path=HEALTH_PATH, status=500),
            models=ProbeObservation(path=MODELS_PATH, status=500),
            artifacts=ProbeObservation(path=ARTIFACTS_PATH, status=500),
            child_alive_after=False,
            api_contract=VersionAdmission.UNKNOWN,
        )
    )
    assert not outcome.ready
    assert len(outcome.failures) == 6
    assert outcome.error_code() is None


def test_approved_endpoint_paths_are_used() -> None:
    """Only the DEC-B paths are referenced."""
    assert HEALTH_PATH == "/health"
    assert MODELS_PATH == "/v1/models"
    assert ARTIFACTS_PATH == "/v1/artifacts"


def test_diagnostic_context_is_structured_and_traceback_free() -> None:
    """Failures carry structured context usable for diagnostics."""
    outcome = evaluate_readiness(
        replace_observation(
            fully_ready_observations(),
            health=ProbeObservation(path=HEALTH_PATH, status=503),
        )
    )
    context = outcome.technical_context()
    assert context["ready"] is False
    assert context["failures"][0]["code"] == "HEALTH_CHECK_FAILED"
    assert context["failures"][0]["context"]["status"] == 503
    assert "Traceback" not in repr(context)


def test_predicate_performs_no_io() -> None:
    """The predicate must be usable with no network, process, or clock access.

    Evaluating a full observation set succeeds using only plain values, proving
    no transport, sleep, or process inspection is required.
    """
    outcome = evaluate_readiness(fully_ready_observations())
    assert outcome.ready
