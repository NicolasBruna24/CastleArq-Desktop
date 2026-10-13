# ADR 0003 — Pending Startup Result and Three-Way Process Observation

- **Status:** Accepted — conversationally ratified
- **Scope:** CastleArq Desktop lifecycle manager, specifically `src/castlearq_desktop/core/lifecycle_manager.py`
- **Date:** 2026-10-11
- **Relates to:** `docs/wp2b-closure-record.md`; `docs/adr-0002-application-exit-coordination.md`
- **Decision basis:** The user conversationally ratified the pending-startup and three-way observation design on 2026-10-11. This ADR records that design decision; it does not authorize implementation.
- **Authorization boundary:** This document records a decision only. It does not authorize implementation, testing, builds, dependency changes, Git operations, or access to CastleArq Core.

## 1. Context

The WP2B closure record documents the completed verification of its approved C1–C8 scope. It remains a historical verification record and is not amended by this ADR.

The current lifecycle manager has a gap in its initial process observation. When the initial observation cannot confirm either that the child is alive or that it has exited, `_adopt_and_observe()` currently treats the result as though liveness were confirmed. This can cause the manager to report `RUNNING` with `status="ok"` despite having only an unknown observation.

The existing `_handle_exited()` path also collapses the `alive` and `unknown` outcomes, preventing adoption logic from distinguishing confirmed liveness from uncertainty.

This ADR records the ratified design direction for those semantic gaps. It does not claim that the implementation has been changed or verified.

## 2. Decision

### Pending startup result

When the initial observation after obtaining a process handle is `unknown`, `start()` must return a result with:

- `status="pending"`
- `state=STARTING`
- `error=None`

This result means that the launch was accepted and a process handle was obtained, establishing ownership of that handle, but the child's operational status has not yet been confirmed.

A pending result is neither a successful startup confirmation nor a startup failure.

The `pending` status already exists in the lifecycle manager's result vocabulary. This decision introduces no new status value.

### Resolution through subsequent observation

- Confirmed liveness permits the transition from `STARTING` to `RUNNING`.
- Confirmed exit follows the existing confirmed-exit transition for a process in `STARTING`.
- Continued `unknown` observation preserves `STARTING`.
- An unknown observation must never be interpreted as proof of either liveness or termination.

### Explicit three-way observation

Adoption logic must distinguish these three outcomes:

- `alive`: liveness is confirmed.
- `exited`: termination is confirmed.
- `unknown`: neither liveness nor termination is confirmed.

The `alive` and `unknown` outcomes must not be collapsed into a single result.

### Initial observation deadline remains unresolved

This ADR does not select or introduce an initial observation deadline.

No timeout duration, retry policy, or deadline is implied by this decision. The initial observation deadline remains a separate, unresolved decision and must be addressed independently if the user authorizes that work.

## 3. Preserved guarantees

The following existing guarantees remain unchanged:

- **Ownership versus operational status:** Obtaining a process handle establishes ownership of that handle, but does not by itself establish that the process is alive.
- **Unresolved ownership:** The existing unresolved-ownership condition remains distinct from a pending result and retains its existing conservative behavior.
- **Duplicate start:** A duplicate start remains refused while the lifecycle manager is in `STARTING`.
- **Unknown status:** Unknown status is not proof of exit. The handle is retained while status remains unknown.
- **Shutdown behavior:** Existing fail-closed shutdown behavior and the distinction between confirmed and unconfirmed exit remain unchanged.
- **Shutdown timing and signaling:** The existing application-level shutdown deadline, graceful period, escalation period, and direct-child signaling restrictions remain governed by the existing shutdown decisions, including ADR-0002. This ADR changes none of them.
- **Existing vocabularies:** This decision introduces no new lifecycle state, `ErrorCode`, or result-status value.

These statements preserve existing constraints; they do not authorize new shutdown behavior or changes to the existing shutdown contract.

## 4. Relationship to existing governance

- `docs/wp2b-closure-record.md` remains unchanged and continues to record the historical WP2B verification evidence for its approved scope.
- `docs/adr-0001-core-launch-contract.md` remains unchanged.
- `docs/adr-0002-application-exit-coordination.md` remains unchanged. Its existing ownership and shutdown constraints continue to apply.

This ADR records a subsequent lifecycle design decision without retroactively changing the scope or claims of the WP2B closure record.

## 5. Non-goals and authorization boundary

This ADR authorizes no implementation.

It does not authorize a work package, source-code changes, tests, builds, dependency changes, adapters, GUI integration, new process-control capabilities, or Git operations.

It does not resolve the initial observation deadline, select a timeout, introduce automatic retries, or change shutdown behavior.

The design recorded here remains a documentation-level decision until implementation is separately and explicitly authorized.
