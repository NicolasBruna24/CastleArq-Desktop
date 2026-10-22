# CastleArq Desktop — Experimental Preview 0.1.0

CastleArq Desktop is a Linux-first PySide6 desktop application for working
with CastleArq Core models locally: it lets the user select an explicit
Python interpreter, launch Core as a Desktop-owned process, explore the
model catalog, acquire models through Core, manage the local artifact
library, and hold real chat sessions against a running Core.

## Current capabilities

Implemented and covered by automated tests:

- **Interpreter configuration** — an explicit absolute path selected by the
  user and persisted via `QSettings` under the approved settings key. No
  `PATH` lookup, no automatic interpreter fallback, and no automatic Core
  installation.
- **Bounded interpreter validation** — an isolated (`python -I`), single-attempt
  diagnostic (5 s hard timeout) that checks the interpreter's Python version
  (minimum 3.11) and whether the `castlearq` distribution and package are
  installed in that interpreter.
- **Single process launch** — Core is started exactly once per accepted startup
  as `[<python>, "-m", "castlearq.main", "serve"]`, without a shell, with
  `stdin`/`stdout`/`stderr` redirected to the null device.
- **Lifecycle observation** — a 100 ms Qt timer drives read-only observation of
  the owned process across the eight approved lifecycle states (`NOT_STARTED`,
  `STARTING`, `RUNNING`, `STOPPING`, `STOPPED`, `START_FAILED`,
  `UNEXPECTED_EXIT`, `SHUTDOWN_FAILED`). An unexpected process exit is detected
  and reported.
- **Asynchronous HTTP readiness checks** — non-blocking Qt Network requests
  against the fixed loopback endpoint `http://127.0.0.1:8000` (see
  [Readiness and compatibility admission](#readiness-and-compatibility-admission-dec-a)).
- **Coordinated shutdown** — window close routes through the application
  controller: graceful termination first, one forced termination inside the
  approved window, an explicit `SHUTDOWN_FAILED` terminal outcome with a
  non-zero application exit code if exit is not confirmed within the 5-second
  total budget, and no further signals after the deadline.
- **Status feedback** — the main window shows lifecycle states and readiness
  outcomes through a status line; startup and shutdown are requested through
  the application controller, never by the UI directly.
- **Model catalog explorer** — browse the models Core reports, read their
  metadata (provider, family, task, architecture, runtimes, supported
  quantizations), pick an announced quantization variant, and see the local
  availability already reported by Core for each model.
- **Model acquisition** — request a download of the selected model/variant
  through CastleArq Core; the result (destination, store state, verification
  status) is shown in the explorer, and the local library refreshes
  afterwards.
- **Local artifact library** — list the artifacts Core reports as installed
  (file, model, observed quantization, store state, problem details when
  Core reports them), select the exact artifact to use, and keep that
  selection stable across library refreshes.
- **Real chat sessions** — open a session against the running Core bound to
  the selected artifact, send consecutive turns whose history Core keeps,
  and close the session explicitly, by changing artifact, or by closing the
  window (which deletes the Core session). Transport, HTTP and Core errors
  are reported with actionable messages.

## Architecture

High-level layering, top to bottom (all packages under `src/castlearq_desktop/`):

- `app.py` — composition root. Builds the production dependencies (settings
  store, monotonic clock, startup coordinator configured with
  `VersionAdmission.NOT_EVALUATED`), owns the application-level
  `StartupController` for the whole run, and maps a terminal `SHUTDOWN_FAILED`
  outcome to a non-zero process exit code.
- `ui/main_window.py` — presentation only. Interpreter selection, a *Start
  Core* action, lifecycle/readiness status, the model catalog explorer, the
  local artifact library, and the chat view. Close handling defers to the
  controller while shutdown is pending and deletes any open chat session in
  Core first. The UI never owns processes, HTTP requests, or persistence.
- `core/startup_controller.py` — application controller (`QObject` with Qt
  signals). Orchestrates a single startup attempt, retains the asynchronous
  readiness checker for the whole operation, suppresses stale results, and
  coordinates the application-level 5-second shutdown deadline without
  blocking the Qt event loop.
- `core/startup_coordinator.py` — small glue layer: validate → launch →
  observe → readiness. Derives read-only process liveness from observable
  manager state for the readiness checks.
- `core/lifecycle_manager.py` — process lifecycle state machine (single
  launch, observation, shutdown escalation, terminal outcomes).
- `core/lifecycle_observer.py` — 100 ms `QTimer` driving
  `LifecycleManager.observe()` from the Qt event loop.
- `core/subprocess_adapter.py` — the concrete `subprocess.Popen` process
  adapter (no shell, null-device stdio, definite-vs-uncertain spawn-error
  classification).
- `core/version_admission.py`, `core/version_probe.py` — the DEC-A version
  policy and, separately, the worker that resolves the really-installed
  version off the GUI thread and delivers it back through a queued Qt signal.
- `core/readiness_qt_transport.py` — asynchronous readiness engine over
  `QNetworkAccessManager` and `QTimer` (per-request timeout, global deadline,
  polling, exactly-once terminal delivery).
- `core/readiness_client.py` — readiness evaluation plus a legacy synchronous
  HTTP engine retained for compatibility; production readiness uses the Qt
  transport.
- `core/interpreter_settings.py`, `core/interpreter_validator.py` — settings
  persistence and the bounded validation diagnostic.
- `core/contracts/` — pure, dependency-free contracts (readiness evaluation,
  deadline arithmetic, shutdown budgets, the nine-code error vocabulary,
  interpreter validation, settings key, port constants). Higher layers depend
  on these contracts rather than on each other's internals.
- `core/http_client.py`, `core/process_manager.py`,
  `storage/conversation_store.py` — boundary placeholders whose operations
  raise `NotImplementedError` by design; no network, process, or database
  activity occurs through them.

## Interpreter configuration and launch

- The Core interpreter is **configured explicitly** as an absolute executable
  path. Non-absolute paths are rejected before anything is executed; Desktop
  never searches `PATH`, never installs Core, and never silently falls back to
  another interpreter.
- Validation runs the selected interpreter once (`-I` isolated mode, exactly
  one attempt, 5-second hard timeout) and classifies the result: an
  unsupported Python version (below 3.11) or a malformed diagnostic yields
  `INTERPRETER_INVALID`; a valid interpreter without the `castlearq`
  distribution/package yields `CORE_NOT_INSTALLED`.
- A validated launch executes exactly
  `[<absolute-python>, "-m", "castlearq.main", "serve"]` — one process per
  accepted startup attempt, without a shell.

## Readiness and compatibility admission (DEC-A)

Desktop performs asynchronous HTTP readiness checks against the fixed loopback
endpoint `http://127.0.0.1:8000`:

- `GET /health` — Core liveness endpoint.
- `GET /v1/models` — model-list API probe.
- `GET /v1/artifacts` — artifacts API probe.

Timing follows the ratified contract: a 20-second global deadline, at most
10 seconds per readiness request and at most 4 seconds for any other HTTP
request (both always bounded by the remaining global budget), a nominal
250 ms polling interval, and no automatic transport retries.

**Compatibility admission (DEC-A) is fail-closed and conservative.** For
every readiness pass Desktop:

- queries the installed `castlearq` distribution version using **only** the
  explicitly selected interpreter (`<python> -I -c` with a stdlib-only
  `importlib.metadata` probe; absolute path, no PATH fallback, no
  alternative interpreter);
- reads the top-level `version` string field of the `GET /health` JSON
  object (strict syntax; any HTTP error, invalid JSON, non-object body, or
  missing/invalid field fails closed);
- requires both versions to be valid **and equal**;
- requires the matched version to belong to the centralized allowlist
  `SUPPORTED_CORE_VERSIONS` in `core/version_admission.py`.

Any unknown, mismatched, invalid, or missing version blocks `READY` with
`API_CONTRACT_INCOMPATIBLE` (existing vocabulary; no new error code), even
when `/health`, `/v1/models`, and `/v1/artifacts` all return HTTP 2xx.
Production composition keeps `VersionAdmission.NOT_EVALUATED` as the static
input; DEC-A dynamics engage through the coordinator's installed-version
provider, so production `READY` is unreachable until a version is both
matched and allowlisted.

The allowlist explicitly admits two versions: `0.5.1` and `0.6.0`,
ratified under DEC-A (see `docs/adr-0005-core-version-admission.md`).
Admission is exact-version only (no ranges, no wildcards): any other
version, including newer Core releases from PyPI or GitHub, is not
automatically compatible and stays fail-closed until it is separately
audited, verified against integration requirements, and explicitly added
to the allowlist. Automated tests with doubles and an explicit test-only
allowlist entry remain isolated verification of the policy logic.

## Lifecycle and shutdown

- Startup performs exactly one accepted attempt or returns a typed failure. A
  launch whose initial observation is unknown remains `STARTING` under periodic
  observation until a later observation confirms liveness (`RUNNING`) or exit
  (`UNEXPECTED_EXIT`).
- Closing the application requests shutdown once: repeated requests are
  coalesced and never reset the deadline or duplicate termination signals.
- The shutdown budget is graceful termination (2 s) followed by a single forced
  termination inside the remaining window; if exit is not confirmed by the
  5-second total budget the outcome is `SHUTDOWN_FAILED` and the application
  exits with a non-zero code. No further signals are sent after the deadline.
- These behaviors are verified with deterministic fakes and with purpose-built
  short-lived test subprocesses. In addition, the opt-in runtime integration
  suite (`CASTLEARQ_RUNTIME_IT=1`) launches, readiness-checks, and shuts down
  a real CastleArq Core `0.5.1` child through the production path
  (`test_dec_a_admitted_real_core_reaches_ready`). Failure and escalation
  paths not exercised there — forced-termination escalation, unconfirmed
  shutdown, unexpected exit — remain covered only by fakes and purpose-built
  subprocesses.

## Setup

Requirements: Linux; Python 3.11 or newer; PySide6 6.5 or newer; and
CastleArq Core **either `0.5.1` or `0.6.0`, installed separately in the interpreter you
select** (Desktop never installs or upgrades Core for you — see the Core
project's own installation instructions). Other
platforms are not validated and no native installers (AppImage, `.deb`) are
provided in this preview.

Install the package (this installs PySide6 and the `castlearq-desktop` entry
point, `castlearq_desktop.app:main`):

```bash
python -m pip install .
```

Run the application:

```bash
python -m castlearq_desktop
# or, if installed:
castlearq-desktop
```

To actually start Core, select an absolute interpreter path in the window that
has the `castlearq` package installed, then press *Start Core*.

## First run

1. Start the application with `castlearq-desktop` (or
   `python -m castlearq_desktop`).
2. Click **“Select Python interpreter…”** and choose the absolute path of the
   Python interpreter that has an admitted CastleArq Core version (`0.5.1` or `0.6.0`) installed.
   The choice is persisted via `QSettings`; nothing is launched until you request it.
3. Click **“Start Core”**. If the interpreter path is invalid, Core is not
   installed there, its version is not an admitted version (`0.5.1` or `0.6.0`), Core is unreachable,
   or readiness fails, the status line reports the cause with an actionable
   message (missing distribution, version mismatch, transport failure, or
   timeout) instead of starting.
4. Once Core is ready, the model catalog explorer fills in: pick a model and
   quantization variant, acquire it through Core, select the resulting
   artifact in the local library, and open a chat session against it.
   Closing the window deletes any open chat session in Core first, then
   shuts Core down within a bounded (5-second) coordinated shutdown.

## Testing

Two suites exist, with different purposes:

- **Unit and contract tests** (default):

  ```bash
  python -m pytest
  ```

  Deterministic; processes, clocks, timers, and HTTP are faked or simulated.
  Tests that require Qt skip automatically when PySide6 is unavailable. The
  test extra (`pip install -e ".[test]"`) is for development only; running
  the application itself needs just the base install plus Core `0.5.1` or `0.6.0`.

- **Runtime integration tests** (opt-in only):

  ```bash
  CASTLEARQ_RUNTIME_IT=1 .venv/bin/python -m pytest -v tests/test_runtime_integration.py
  ```

  Most cases exercise the real Qt event loop, a real
  `QNetworkAccessManager` against a loopback-only HTTP test server on an
  ephemeral port, and short-lived purpose-built test subprocesses (never
  CastleArq Core). One dedicated case,
  `test_dec_a_admitted_real_core_reaches_ready`, goes further: under explicit
  authorization it launches a real, isolated CastleArq Core `0.5.1`
  installation through the real coordinator, the real Qt transport, and the
  production allowlist, and requires readiness to really reach `READY`. All
  cases are skipped unless `CASTLEARQ_RUNTIME_IT=1` is set. That real-Core
  case validates the launch → readiness → shutdown flow and the three
  approved endpoints (`/health`, `/v1/models`, `/v1/artifacts`) against Core
  `0.5.1` only; it does not cover other Core versions or the rest of Core's
  API surface.


## Known limitations

- **DEC-A admits exactly two versions** — production readiness admission is
  fail-closed (`VersionAdmission.NOT_EVALUATED` as the static input plus
  conservative per-pass version comparison), so a readiness success state
  requires a matched version that is explicitly allowlisted. Today those are
  `0.5.1` and `0.6.0` (see `docs/adr-0005-core-version-admission.md`);
  every other version — including newer Core releases — stays fail-closed
  until separately audited against contract requirements and explicitly
  added to the allowlist.
- No conversation persistence is implemented: chat history lives in the
  running Core session and is deleted when the session closes; closing the
  window deletes the session.
- No bundled Linux installers (AppImage, `.deb`) are provided; installation is
  via `pip` as described above.
- Interpreter validation is synchronous on the calling thread (bounded to
  5 seconds); non-blocking validation is a separately tracked design concern.
- The installed-version probe used for DEC-A admission runs on a worker, so
  readiness never blocks the GUI thread on it. A probe still in flight can
  outlive the window: closing does not wait for the worker, but the pool and
  interpreter lifecycle may delay the final teardown of the process. Stale
  results are invalidated rather than applied, yet an in-flight probe is not
  cancelled and immediate exit is not guaranteed.
- Port-conflict detection at startup is not implemented (the error code and
  port constants exist in the contracts only).
- Integration evidence is limited to Core `0.5.1` — the opt-in runtime suite
  launches a real, isolated Core `0.5.1` installation through the production
  path (launch, readiness over `/health`, `/v1/models`, `/v1/artifacts`, and
  coordinated shutdown), but no other Core version and no further API surface
  has been exercised against a real installation.
- The legacy synchronous HTTP readiness engine is retained for compatibility;
  production readiness uses the asynchronous Qt transport.

## Project status

Experimental Preview 0.1.0: configuration → validation → launch → observation
→ readiness → coordinated shutdown, plus the model catalog explorer, model
acquisition through Core, the local artifact library, and real chat sessions
against Core, are implemented and covered by a deterministic unit/contract
suite plus the opt-in runtime integration suite. The project is suitable for
a limited experimental preview on Linux with the limitations above.
Conversation persistence, native installers, and universal model/hardware
support remain out of scope for this preview.

## Reporting issues

Please report problems at
[https://github.com/NicolasBruna24/CastleArq-Desktop/issues](https://github.com/NicolasBruna24/CastleArq-Desktop/issues)
(`origin` remote verified locally). Include the Desktop version (`0.1.0`),
the exact Core version installed, the interpreter path selected, the
status-line message shown, and — for chat or library problems — the model id,
artifact filename, and observed quantization/state.

## License

This project is licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).

Copyright 2026 Nicolas Bruna.
