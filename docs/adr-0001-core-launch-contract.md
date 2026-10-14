# ADR 0001 — CastleArq Core Launch and Discovery Contract (Desktop)

- Status: Accepted — Desktop launch policy
- Scope: CastleArq Desktop integration boundary only
- Date: 2026-10-10
- Relates to: Phase 2 (Core launch, discovery, and readiness). This ADR records decisions only; it authorizes no implementation.

> This document records the user-approved policy for how CastleArq Desktop
> locates, validates, launches, and communicates with CastleArq Core. It does
> **not** authorize or imply any change to CastleArq Core, and it does **not**
> claim that any of this behavior is implemented. Phase 2 implementation
> remains a separate, unauthorized step.

## 1. Context

CastleArq Desktop is a PySide6 front-end that will drive CastleArq Core as a
separate service. The Phase 1 skeleton (`src/castlearq_desktop/core/`) currently
contains only boundary placeholders:

- `core/process_manager.py` — `CoreProcessManager` with an unused `executable`
  parameter; `start()`/`stop()` raise `NotImplementedError`; `is_running()`
  returns `False`. No process is ever launched.
- `core/http_client.py` — `CoreHttpClient` with an unused `base_url` parameter;
  `get()`/`post()` raise `NotImplementedError`. No request is ever made.

Before Phase 2 can launch and talk to Core, several contract questions must be
settled: how Desktop finds a Core interpreter, how it launches Core, on what
endpoint, how it decides Core is ready, and how it checks compatibility. This
ADR fixes those decisions so that implementation is unambiguous and safe.

The driving constraints are: explicit configuration over guessing, Desktop
ownership of the Core child process, predictable and actionable failure
behavior, and strict preservation of the Core/Desktop boundary.

## 2. Decision

Desktop will launch CastleArq Core as an explicitly configured, Desktop-owned
child process on a fixed loopback endpoint, and will treat readiness and
compatibility as explicit, verifiable conditions rather than assumptions.

### D1 — Explicit interpreter selection (no PATH-only discovery)

Desktop must **not** rely on PATH-only discovery to locate Core. The user
selects an explicit Python interpreter path, and Desktop persists that selection
locally using Qt `QSettings`.

Before launching Core, Desktop must validate the configured interpreter:

1. The configured executable **exists and can be executed**.
2. Its **Python version is at least 3.11**.
3. The **`castlearq` distribution is installed** in that interpreter, verified
   with `importlib.metadata` (or an equivalent direct distribution-metadata
   check).

`castlearq --version` alone is **insufficient** as the installation check,
because Core's existing version helper can fall back to source-tree metadata
when the installed distribution metadata is absent — so a `--version` success
does not prove an installed, importable distribution.

Desktop must **not** silently install or provision Core. Invalid or missing
configuration must produce an actionable error, never guessed discovery or
automatic installation.

### D2 — Selected launch invocation

The selected Desktop invocation is:

```
<configured-python-interpreter> -m castlearq.main serve
```

This is the Desktop integration decision. The inspected Core source establishes
that `castlearq.main` invokes `main()` when executed as a module, and that
`serve` is a registered command. This ADR does **not** claim that
`python -m castlearq` is supported.

This selected invocation is deliberately distinct from Core's installed
console-script entry point `castlearq = castlearq.main:main`. Desktop invokes
the module (`-m castlearq.main serve`) through the user-selected interpreter
rather than shelling out to the `castlearq` console script, so that the exact
interpreter (and therefore the environment in which Core runs) is controlled
and validated by Desktop.

### D3 — Fixed loopback endpoint

Desktop launches Core on:

- Host: `127.0.0.1`
- Port: `8000`

No automatic port fallback, port scanning, or silent port switching is
permitted.

If startup fails because the port is occupied, Desktop must report a **startup
conflict**. It must not:

- attach to an unrelated server,
- terminate an unrelated process, or
- treat an existing listener as its own Core child.

### D4 — Child ownership and readiness

Desktop may communicate with the launched service **only as a process it
explicitly started and continues to own**.

Readiness requires **both**:

- a successful `GET /health` response, **and**
- continued liveness of the owned child process.

Startup timeout must not exceed **20 seconds**, the polling interval is
**250 ms**, and premature child exit must be detected (an early child exit ends
the readiness wait and is reported, not retried indefinitely).

### D5 — Compatibility checks

Version information alone does **not** establish compatibility. Compatibility
validation must include the `/health` version field **and** the required
API-contract probes.

The exact accepted version policy and the precise probe assertions are
implementation details still to be finalized before or during a separately
authorized implementation-planning step. This ADR deliberately does **not**
invent a compatibility matrix.

### D5.1 — Conservative admission DEC-A (ratified 2026-10-13)

DEC-A implements the D5 policy conservatively (fail-closed, existing
`API_CONTRACT_INCOMPATIBLE` vocabulary, no new error code):

- The installed `castlearq` distribution version is obtained with **only**
  the explicitly selected interpreter (isolated `-I` stdlib
  `importlib.metadata` probe; absolute path, no PATH fallback, no
  alternative interpreter).
- The Core version is the top-level `version` string field of the
  `GET /health` JSON object (user-confirmed shape 2026-10-13). Any HTTP
  error, invalid JSON, non-object body, or missing/invalid field fails
  closed.
- Both versions must be valid **and equal**, and the matched version must
  belong to the centralized allowlist `SUPPORTED_CORE_VERSIONS`
  (`src/castlearq_desktop/core/version_admission.py`).
- The allowlist was initially **empty** (user decision 2026-10-13): no
  version admitted `READY` and `0.5.1` was a pending candidate under real
  verification.
- Update (user decision 2026-10-13, since validated): after real
  verification against an isolated Core `0.5.1` installation (installed
  version equals the `/health` version across launch/readiness, plus
  readiness success), `0.5.1` was explicitly admitted; the allowlist now
  contains exactly `0.5.1` and no other version. Admission is
  exact-version only — no ranges, no wildcards — and no other Core
  version, including newer releases, is automatically compatible.
- Automated tests (doubles + explicit test-only allowlist entries) remain
  isolated verification of the policy logic. Admitting any new version
  requires real verification against an actual Core install, then an
  explicit allowlist addition; until that happens every other version
  stays fail-closed. DEC-A has been positively validated against a real
  Core `0.5.1` installation through the opt-in runtime integration
  suite (real coordinator, real Qt transport, real allowlist).

### D6 — No Core changes

These decisions apply to the Desktop integration boundary only. They do **not**
authorize any change to CastleArq Core, and none is implied.

## 3. Alternatives considered

- **PATH-only discovery** (locate a Python or a `castlearq` command via `PATH`).
  Rejected: it is ambiguous, environment-dependent, and can silently select the
  wrong interpreter or a different Core installation. Explicit, persisted
  selection (D1) is predictable and auditable.
- **Silently installing or provisioning Core.** Rejected: hidden mutation of the
  user's environment and masking of real misconfiguration. Errors must be
  actionable (D1).
- **Attaching to an already-running server.** Rejected: Desktop could not own or
  reason about a process it did not start, and could act on an unrelated
  service. Ownership is required (D4).
- **Automatically selecting another port / port scanning.** Rejected: silent
  relocation hides conflicts and breaks the fixed-endpoint expectation. A
  port-bind failure is treated as an authoritative startup conflict (D3).
- **Using `castlearq --version` as the sole installation check.** Rejected: the
  Core version helper can fall back to source-tree metadata, so it does not
  prove an installed distribution. A distribution-metadata check via
  `importlib.metadata` is required (D1).


## 4. Consequences

**Positive**

- Configuration is explicit and persisted, so launches are reproducible and
  debuggable.
- Desktop always owns the Core child it talks to, so readiness and shutdown
  reasoning are sound.
- Failure modes are explicit: missing/invalid interpreter, occupied port, and
  early child exit each produce a specific, actionable outcome instead of a
  hang or a silent fallback.
- The Core/Desktop boundary is preserved: Core is treated as an external service
  driven through a fixed contract; Desktop changes nothing inside Core.

**Costs / trade-offs**

- First-run requires the user to select and Desktop to validate an interpreter;
  there is no zero-configuration "just works" path.
- The endpoint is fixed, so concurrent Core use on port 8000 will surface as a
  startup conflict rather than being worked around automatically.
- `castlearq.main serve` is coupled to Core's current module/command surface;
  any future Core change to that surface is a Desktop integration change.

## 5. Requirements and invariants

1. Core interpreter is explicitly selected by the user and persisted via
   `QSettings`; PATH-only discovery is not used. (D1)
2. Pre-launch validation: executable exists and is runnable; Python >= 3.11;
   `castlearq` distribution present (via `importlib.metadata` or equivalent).
   (D1)
3. No silent Core installation or provisioning; invalid/missing config yields an
   actionable error. (D1)
4. Launch invocation is exactly `<configured-python-interpreter> -m castlearq.main serve`. (D2)
5. Endpoint is fixed at host `127.0.0.1`, port `8000`; no fallback/scan/switching. (D3)
6. Port-bind failure is a startup conflict; Desktop never attaches to or
   terminates an unrelated server/process. (D3)
7. Desktop communicates only with a child it explicitly started and owns. (D4)
8. Readiness = successful `GET /health` **and** owned child still alive; timeout
   <= 20 s; poll interval 250 ms; premature child exit detected. (D4)
9. Compatibility uses the `/health` version field plus required API-contract
   probes; version alone is insufficient. (D5)
10. No CastleArq Core changes are authorized or implied. (D6)

## 6. Evidence and verified premises

- Desktop side (current repository, Phase 1 boundary only):
  - `src/castlearq_desktop/core/process_manager.py` — `executable` parameter
    stored but unused; `start()`/`stop()` raise `NotImplementedError`;
    `is_running()` returns `False`.
  - `src/castlearq_desktop/core/http_client.py` — `base_url` parameter stored
    but unused; `get()`/`post()` raise `NotImplementedError`.
  - These confirm the seams exist and are unimplemented; they contain no launch,
    discovery, port, or readiness logic.
- Core side (premises supplied by the user as already-inspected and ratified;
  not re-inspected for this ADR, and Core was not accessed):
  - `castlearq.main` invokes `main()` when executed as a module.
  - `serve` is a registered command, so `python -m castlearq.main serve` is a
    valid invocation.
  - The installed console-script entry point is `castlearq = castlearq.main:main`.
  - Core's version helper can fall back to source-tree metadata when installed
    distribution metadata is absent (the reason `castlearq --version` alone is
    insufficient).

No claim is made here that Core was read or modified as part of recording this
ADR.

## 7. Remaining implementation details (not decided here)

These are intentionally left open and are **not** authorized by this ADR:

1. The exact accepted version policy for `/health.version` (minimum/maximum
   compatible Core version, and how unknown/newer versions are handled). (D5)
2. The precise set and assertions of the required API-contract probes beyond
   `/health`. (D5)
3. The concrete `QSettings` key names, storage layout, and any first-run UX for
   interpreter selection and validation error presentation. (D1)
4. The exact error taxonomy and user-facing messages for each failure mode
   (invalid interpreter, occupied port, early child exit). (D1, D3, D4)

These will be finalized in a separately authorized implementation-planning step.

## 8. Phase 2 implementation status

**Not implemented and not authorized by this ADR.** The decisions above are a
contract for future work. No code has been written to select an interpreter,
launch Core, poll for readiness, or perform compatibility checks.

## 9. Amendment A1 — Interpreter validation isolation (approved)

- Status: Accepted — human-approved policy decision ("Option A")
- Date: 2026-10-10
- Scope: Extends D1 and fixes the DEC-I diagnostic command. Amends no other
  decision; in particular D2 (launch invocation) is unchanged.

This amendment records a decision taken in a read-only review of the interpreter
diagnostic contract. It distinguishes normative decision text from rationale and
consequences.

### A1.1 Decision (normative)

1. Interpreter validation retains the approved isolated-mode flag `-I`. The
   validation invocation shape is:

   ```text
   <configured-absolute-python> -I -c <diagnostic-source>
   ```

   executed as an argument list with `shell=False`, exactly one attempt, and a
   maximum execution time of 5 seconds (DEC-I constants).
2. The approved `<diagnostic-source>` is the program recorded verbatim in A1.2.
   Its semantics are fixed as reviewed and accepted; any change requires
   another recorded decision.
3. Isolated validation intentionally excludes the user's site-packages
   directory and ignores Python environment-variable-based module discovery,
   including `PYTHONPATH`.
4. Under the isolated validation environment, when the `castlearq`
   distribution is not discoverable via `importlib.metadata` or the
   `castlearq` package is not discoverable via `importlib.util.find_spec`, a
   valid diagnostic result is classified as `CORE_NOT_INSTALLED` under the
   existing DEC-D vocabulary. No error code is added, renamed, or re-scoped.
5. A Core installation available only through user site-packages or only
   through `PYTHONPATH` is intentionally rejected, even though the D2 launch
   invocation (which does not use `-I`) might otherwise discover it.
6. This rejection is an intentional fail-closed policy, not an accidental
   limitation: validation is deliberately stricter than launch.
7. Validation isolation (D1, DEC-I, this amendment) and launch behavior (D2)
   are distinct. The D2 launch invocation remains exactly
   `<configured-python-interpreter> -m castlearq.main serve`.

### A1.2 Approved diagnostic source (verbatim)

```python
import importlib.metadata as metadata
import importlib.util
import json
import sys

try:
    metadata.distribution("castlearq")
    distribution_found = True
except metadata.PackageNotFoundError:
    distribution_found = False

try:
    package_found = importlib.util.find_spec("castlearq") is not None
except (ImportError, ValueError):
    package_found = False

print(json.dumps({
    "python_version": list(sys.version_info[:3]),
    "distribution_found": distribution_found,
    "package_found": package_found,
}, separators=(",", ":")))
```

The program imports only standard-library modules, never imports or executes
`castlearq`, installs nothing, discovers no alternative interpreter, persists
nothing, and on success exits 0 writing exactly one JSON object with exactly
the keys `python_version` (three integers), `distribution_found` (boolean), and
`package_found` (boolean). Any uncaught exception terminates the process with a
nonzero exit status, which the DEC-I classifier maps to `INTERPRETER_INVALID`.

### A1.3 Rationale (non-normative)

- Reproducibility: `-I` removes environment-dependent variation (user site,
  `PYTHONPATH`) from validation, so validation observes the interpreter's base
  installation environment.
- Fail-closed: rejecting user-site-only or `PYTHONPATH`-only installations
  prevents Desktop from accepting a Core install it did not observe under the
  controlled conditions; the actionable remedy is installing Core into the
  selected interpreter's main environment (e.g. a dedicated virtual
  environment).
- Consistency with D1: validation must prove an installed, importable
  distribution; isolated mode strengthens that proof.

### A1.4 Consequences (non-normative)

- `pip install --user` Core installations, and installations reachable only
  through `PYTHONPATH`, are rejected as `CORE_NOT_INSTALLED` even though D2's
  launch command would find them. Accepted under Option A.
- The direction of strictness is safe: Desktop never launches Core in an
  environment it failed to validate, preserving D1 requirement 2 and D4
  ownership.
- The DEC-I diagnostic command text, previously unspecified in the pure
  contract package (which classifies synthetic results only and continues to
  define no command), is now fixed by this amendment for the Desktop decision
  record.

### A1.5 Non-goals

This amendment authorizes no implementation. It does not create or define any
work package (WP1 remains unauthorized), implements no subprocess runner,
interpreter selection, or settings persistence, and changes no source code,
tests, dependencies, launch invocation (D2), or error-code vocabulary (DEC-D).

