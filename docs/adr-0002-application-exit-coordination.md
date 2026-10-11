# ADR 0002 — CastleArq Desktop Application-Exit Coordination Contract

- **Status:** Accepted — conversationally ratified application-exit coordination policy
- **Scope:** CastleArq Desktop application shell and process lifecycle coordination
- **Date:** 2026-10-11
- **Relates to:** `docs/wp2b-closure-record.md`; `docs/adr-0001-core-launch-contract.md`
- **Decision basis:** DEC-2, Option A, ratified conversationally by the user on 2026-10-11
- **Authorization:** This ADR records a decision only. It does not authorize implementation, testing, builds, Git operations, or CastleArq Core launch.

## 1. Context

The WP2B closure record explicitly defers application-exit cleanup and establishes it as a mandatory gate before CastleArq Desktop may launch CastleArq Core.

The Desktop lifecycle manager defines process-lifecycle and shutdown contracts, but the current application shell does not yet integrate those contracts into the Qt application-exit path.

Application shutdown must therefore coordinate the Desktop-owned direct child without blocking the UI thread synchronously or allowing the Qt event loop to terminate before the coordinated shutdown outcome is determined.

## 2. Decision

DEC-2, Option A, is accepted as the application-exit coordination policy.

### D1. Centralized exit coordination

Normal application-exit requests must be coordinated through a common application-exit coordinator before the Qt main event loop is allowed to terminate.

The policy covers:

- Normal closure requests for the main application window.
- Application-controlled programmatic exit requests, provided they follow the explicit convention defined by D2.

This decision establishes a coordination contract, not a concrete implementation.

### D2. Programmatic exit convention

Application code requesting normal programmatic termination must submit the request through the common coordinator rather than directly terminating the Qt event loop.

Direct calls to `QCoreApplication.quit()` or `QCoreApplication.exit()` that bypass the coordinator are not compliant with this convention and may terminate the event loop before shutdown coordination completes.

This ADR does not claim that Qt automatically intercepts or prevents every direct exit call.

### D3. Non-blocking event-loop coordination

The Qt event loop must remain active while shutdown coordination is in progress.

Shutdown observation must be non-blocking. `QTimer` is a candidate for periodic observation, but this ADR does not mandate a particular timer configuration or authorize implementation.

`QCoreApplication.aboutToQuit` must not be relied upon to initiate a new asynchronous shutdown sequence that requires the main event loop to continue processing events.

### D4. Shutdown timing and process ownership

The existing DEC-F shutdown contract remains unchanged:

- Up to 2 seconds for graceful termination.
- Up to 3 additional seconds for escalation.
- Only the Desktop-owned direct child may be terminated or killed.
- Unknown process status must not be treated as confirmed exit and must not trigger additional termination signals.
- UI-thread synchronous waiting and forced termination of worker threads are prohibited.

Confirmed child exit and exhaustion of the shutdown budget without confirmed exit are distinct outcomes. Timer scheduling and operating-system delays mean that these budgets are application-level coordination deadlines, not guarantees of exact wall-clock termination.

### D5. Failure boundaries

The coordination contract does not guarantee graceful cleanup following an abrupt Desktop crash, forced process termination, or an application-controlled exit path that bypasses the coordinator.

Shutdown failure and unresolved process ownership must not be silently represented as confirmed successful shutdown. An observable reporting mechanism remains a prerequisite to a complete application-exit integration.

### D6. Application-level shutdown failure policy

The application-level shutdown coordination deadline is 5 seconds from the initial shutdown request. If child-process termination cannot be confirmed by that deadline, Desktop must report an explicit shutdown failure and exit with a non-zero application exit code.

Confirmed termination and unconfirmed termination are distinct outcomes. An `unknown` process status must never be treated as confirmed termination, and no additional process-termination signals may be sent to resolve unknown status. An application-level shutdown deadline of this kind is coordination metadata, not evidence that the child process has terminated. The application coordinator must be able to enforce its own deadline without silently changing the existing LifecycleManager state-machine semantics.

The existing DEC-F constraints remain unchanged: up to 2 seconds for graceful termination, up to 3 additional seconds for escalation, and signals may target only the Desktop-owned direct child.

The application shell will eventually need to route compliant exit requests through a common coordinator and preserve event-loop operation while shutdown is pending.

The coordinator's concrete design, Qt integration details, shutdown-failure reporting channel, and implementation sequencing remain unresolved implementation matters. This ADR does not select a specific implementation mechanism beyond the normative coordination contract above.

The policy adds no authority to terminate unrelated processes or process descendants.


### D7. Coordinator design (ratified)

The application-level exit coordinator is constructed from `main()` and remains alive independently of the main window's lifetime.

- **Observation timer:** A `QTimer` with a nominal 100 ms interval drives periodic lifecycle observation without blocking the Qt UI thread.
- **Independent deadline:** The application-level shutdown deadline starts when the first shutdown request is accepted. Elapsed time is measured using a monotonic clock. Timer intervals are not wall-clock guarantees.
- **Repeated requests:** Repeated shutdown requests are coalesced; they must not reset the deadline or duplicate termination signals.
- **Failure reporting:** If termination is not confirmed before the deadline, an explicit error is recorded and the application exits with a non-zero exit code.
- **Strict deadline rule:** Successful termination must be confirmed by an observation before the deadline. If the deadline expires without confirmation, failure becomes terminal and irreversible. Later observations must not change that result.
- **Unknown process status:** `unknown` is never interpreted as confirmed termination. No additional process-termination signals are sent to resolve unknown status.
- **Event loop:** The Qt event loop remains active during asynchronous shutdown coordination. `aboutToQuit` is not relied upon to initiate asynchronous cleanup.
- **Existing shutdown constraints:** DEC-F is preserved: up to two seconds for graceful termination, up to three additional seconds for escalation, and signals target only the Desktop-owned direct child. The Coordinator does not silently alter existing `LifecycleManager` state-machine semantics.
- **Scope boundary:** Abrupt crashes, forced termination, and exit paths that bypass the coordinator remain outside the graceful-shutdown guarantee.


## 4. Relationship to Existing Governance

- `docs/wp2b-closure-record.md` remains unchanged. It records WP2B completion evidence and explicitly defers application-exit cleanup.
- `docs/adr-0001-core-launch-contract.md` remains unchanged. It records the existing Core launch and discovery contract.
- This ADR does not close WP2B's application-exit cleanup gate and does not authorize a successor work package.

## 5. Authorization Boundary

This document records DEC-2, Option A, as conversationally ratified.

**It does not authorize:**

- Source-code changes or implementation.
- GUI lifecycle integration or process-adapter implementation.
- Tests, builds, or application launches.
- Git staging, commits, pushes, branches, or tags.
- Launching CastleArq Core.

Any such work requires separate explicit authorization.