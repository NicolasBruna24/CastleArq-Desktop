# CastleArq Desktop — Core Update Discovery v1 (Specification)

- **Status:** Specification with v1 decisions DEC-UD-1 through DEC-UD-4 **ratified** on 2026-10-13; still does **not** authorize implementation (staged plan in §13)
- **Scope:** CastleArq Desktop; a future, manually-triggered Core update *discovery* feature
- **Date:** 2026-10-13
- **Relates to:** `docs/adr-0001-core-launch-contract.md` (D5 / D5.1, DEC-A admission), `src/castlearq_desktop/core/version_admission.py`, `src/castlearq_desktop/core/readiness_qt_transport.py`
- **Authorization boundary:** This document specifies behavior only. It does not authorize implementation, testing, dependency changes, or any change to DEC-A, the readiness path, or the admission allowlist. The feature described here does **not** exist in the codebase today.

## 1. Purpose

CastleArq Desktop currently admits exactly one CastleArq Core version (`0.5.1`) through the fail-closed DEC-A allowlist, but it gives the user no way to learn whether a newer Core release has been published. This specification defines a first version of a **manual update discovery** check: on an explicit user action, Desktop asks the official PyPI JSON API for the latest stable `castlearq` release and reports how it relates to (a) the installed version and (b) Desktop's local admission allowlist.

Discovery is **informational only**. It never admits a version, never installs or downloads anything, and never alters any existing contract.

## 2. Scope

In scope for v1:

- A single check initiated only by an explicit user action.
- One HTTPS GET to the official PyPI JSON endpoint for the project `castlearq`.
- Determining the latest **stable, valid** published version (no pre-releases in v1).
- Reading the installed version through Desktop's existing installed-version mechanism (explicit interpreter selection respected).
- PEP 440-compatible comparison of installed vs. published versions.
- A reported state that distinguishes, inequivocally: installed version, latest published stable version, and the locally admitted version(s).

Out of scope for v1 (explicitly deferred to later decisions):

- Any implementation (UI, logic, networking).
- Dependency changes, tests, changes to coordinators or the readiness transport.
- Installation, download, or update of Core (or any Python environment mutation).
- Pre-release channels.
- Automatic/scheduled/background checks or any resident process.
- GitHub or any source other than PyPI.

## 3. Architectural invariants

These invariants are non-negotiable for v1 and any successor:

1. **Discovery is not admission.** A published version becomes usable only through the existing DEC-A path: exact match of the really-installed version and the real `/health` version, membership in the explicit `SUPPORTED_CORE_VERSIONS` allowlist, and readiness success. Discovery output never feeds `classify_version_admission` / `evaluate_version_admission`.
2. **Admission is not installation.** Even an admitted published version requires the user to provision it themselves; Desktop never installs or upgrades Core.
3. **DEC-A is untouched.** The allowlist currently containing exactly `"0.5.1"` is read-only for this feature; it is neither modified nor bypassed.
4. **Fail-closed posture carries over.** Every unknown, malformed, timed-out, or inconsistent discovery input resolves to a "search failed" outcome — never to "an update exists, take it".
5. **No hidden behavior.** No automatic checks, no timers, no background workers, no persistence of results in v1.

## 4. Terminology — the three versions

The feature must keep three distinct quantities, never conflated in data model or UI copy:

| Term | Meaning | Source |
| --- | --- | --- |
| **Installed version** | The `castlearq` distribution version in the *explicitly selected* Core interpreter, or unknown | Existing `query_installed_core_version()` (isolated `-I` stdlib `importlib.metadata` probe; absolute path, no PATH fallback) |
| **Latest published stable** | The highest PEP 440 *final* version published on PyPI for project `castlearq`, with at least one distribution file, excluding pre-releases and invalid version strings | PyPI JSON API (this feature) |
| **Admitted version** | Version(s) in Desktop's local DEC-A allowlist, currently exactly `{"0.5.1"}` | `SUPPORTED_CORE_VERSIONS` (read-only snapshot) |

"Installed" answers *what am I running*; "published" answers *what exists upstream*; "admitted" answers *what Desktop will allow to reach READY*. Only the third gates production readiness.

## 5. Existing components and conventions reused

- **Installed-version probe:** `castlearq_desktop.core.version_admission.query_installed_core_version(python_path)` with its existing bounded subprocess timeout (`MAXIMUM_VALIDATION_TIMEOUT_SECONDS`, 5 s). No second probe is invented.
- **Interpreter selection:** `InterpreterSettingsStore.load()` — the explicit selection only; `None` (unconfigured) stays a first-class outcome. No PATH lookup, no discovery of interpreters.
- **Allowlist:** `SUPPORTED_CORE_VERSIONS` (exact strings, no ranges/wildcards), read once per check into a snapshot for comparison and display.
- **Bounded single-request convention:** analogous to DEC-E's `MAX_REQUEST_TIMEOUT_SECONDS` (4.0 s) for readiness; discovery proposes its own explicit cap (see §8), never an unbounded wait.
- **Bounded body-read convention:** analogous to `MAX_HEALTH_BODY_BYTES`, but with a larger explicit cap suited to PyPI metadata (see §8).
- **Version comparison (DEC-UD-1, ratified):** PEP 440 comparison uses the `packaging` distribution, to be declared as a **runtime dependency** in the stage that lands the service (this specification changes no dependency). The project will not rely on `packaging` being present transitively, and no hand-rolled comparator will be written. The exact minimum supported `packaging` version is chosen at implementation time by the rule in §6.2 and recorded with the dependency change; invalid version strings are handled fail-closed via `packaging.version.InvalidVersion` (§9).
- **Transport posture:** stdlib `urllib` only at runtime — `httpx` is not declared in `pyproject.toml` and no further dependency beyond `packaging` (DEC-UD-1) is introduced by this specification. Asynchronous delivery to the UI follows the established Qt pattern (`QNetworkAccessManager` + signals, as in `readiness_qt_transport.py`), so the GUI thread never blocks.
- **Error style:** typed failures with a stable machine-readable reason, an actionable user-facing message, and structured technical context; no stack traces or raw payloads in user-facing messages (the `CastleArqError` conventions). The global `ErrorCode` catalog (DEC-D) stays unchanged; discovery uses its own local error type (DEC-UD-3, §9).
- **Documentation style:** this file follows the existing `docs/` conventions (flat layout, dated, explicit authorization boundary).

## 6. Trigger and flow

### 6.1 Trigger

The check runs only when the user explicitly requests it, from the dedicated **Settings → Updates** section (DEC-UD-4, §11). Prohibited triggers in v1: application start, timer, Core launch, readiness completion, settings change, or any background scheduling. A configured interpreter is **not** required to run the check: without one — or when the installed version cannot be resolved — the published information is still reported when available, with the installed version marked unknown (§7 `INSTALLED_UNKNOWN`).

### 6.2 Flow

1. **Snapshot inputs.** Read the configured interpreter path (`InterpreterSettingsStore.load()`) and a copy of `SUPPORTED_CORE_VERSIONS`. Inputs are snapshotted at trigger time so a mid-flight settings change cannot produce a mixed result.
2. **Resolve installed version.** Call `query_installed_core_version(snapshot_path)`. Result: a valid version string, or unknown (`None`). The interpreter subprocess is bounded by its existing 5 s cap and never blocks the GUI thread (executed off the GUI thread or via the existing injectable-execution pattern).
3. **Query PyPI.** One HTTPS GET to the fixed official endpoint `https://pypi.org/pypi/castlearq/json`, with the limits of §8. No other host, no other URL, no authentication, no cookies.
4. **Validate the payload.** Enforce HTTP 200, JSON decoding, the schema checks of §8.2, and the "at least one usable stable version" requirement.
5. **Select the latest stable version.** From the `releases` mapping: parse each version key with `packaging` (DEC-UD-1; unparseable keys are discarded as invalid metadata and recorded in technical context); discard pre-releases; discard entries with no distribution files or whose files are all yanked/withdrawn (the documented policy of §8.2 — "latest stable" means *installable and not withdrawn*); take the maximum of the remainder under PEP 440 ordering. `info.version`, when present and valid, is only a cross-check; the computed maximum is authoritative, and a mismatch is recorded in technical context rather than trusted blindly.
6. **Compare and classify.** Using PEP 440 ordering, compare installed vs. latest stable and consult the allowlist snapshot to produce exactly one state from §7.
7. **Report.** Deliver the result object (state + the three version fields + failure reason when applicable) to the UI. Presenting the result must never offer an action that implies installation by Desktop.

**PEP 440 engine (DEC-UD-1).** All version parsing, normalization, ordering, and pre-release detection is delegated to `packaging` (`packaging.version`); Desktop implements no comparison logic of its own. The minimum compatible `packaging` version is determined at implementation time as follows: choose the newest stable `packaging` release whose supported-Python range covers Desktop's floor (Python 3.10+), verify with an isolated contract check that the required API behaves as specified (invalid strings raise `InvalidVersion`; pre-releases are flagged as such), and record the exact lower bound (e.g. `packaging>=X`) in `pyproject.toml` together with the rationale, in the same change that lands the service. Invalid version strings — PyPI keys, `info.version`, or the installed-version probe — are treated as *unknown* (fail-closed), never as a comparable or admitted version.

## 7. States and result model

Proposed internal name: `CoreUpdateDiscoveryState` (an enum mirroring the naming style of `VersionAdmission`). Every result carries `installed_version: str | None`, `latest_stable: str | None`, and `admitted_versions: frozenset[str]` (snapshot), so the UI can always show the three-way distinction even when a state is degenerate.

| State | Precise condition | User-facing meaning |
| --- | --- | --- |
| `IN_PROGRESS` | A check was triggered and no terminal result delivered yet | A search was requested and is running; nothing is implied about versions |
| `LATEST_INSTALLED` | installed known ∧ installed == latest stable | The installed version is the latest published stable version |
| `NEWER_STABLE_ADMITTED` | installed known ∧ latest stable > installed ∧ latest stable ∈ allowlist | A newer stable version exists **and** Desktop admits it; installing it yourself would be allowed to reach READY (still never performed by Desktop) |
| `NEWER_STABLE_NOT_ADMITTED` | installed known ∧ latest stable > installed ∧ latest stable ∉ allowlist | A newer stable version exists but Desktop does **not** admit it; it must **not** be presented as a compatible update — READY would stay blocked after any manual install |
| `INSTALLED_AHEAD` | installed known ∧ installed > latest stable | The installed version is newer than the latest published stable (pre-release, local build, or mirror lag); no update action is implied |
| `INSTALLED_UNKNOWN` | installed unresolved (unconfigured interpreter, probe failure, invalid reported version) | Desktop cannot determine what is installed; the upstream result may still be reported when the PyPI query succeeded |
| `SEARCH_FAILED` | PyPI query failed or the payload unusable (see §9) | The search did not complete; no conclusion about published versions exists |

Additional rules:

- `IN_PROGRESS` is the only non-terminal state; all others are terminal for that trigger.
- Exactly one terminal state per trigger. At most one query is active at a time: a second user trigger while one runs does **not** start a second request — the UI keeps showing the current operation's state (DEC-UD-4, §11).
- A result delivered after the §8 total deadline has expired, or belonging to a superseded operation, is **dropped**: it must never update the UI (DEC-UD-2).
- The word "update" appears in user-facing copy only as "*a newer version is published*"; Desktop never offers "Update" as an executable action in v1.
- `NEWER_STABLE_NOT_ADMITTED` copy must state explicitly that the published version is **not** admitted and that Desktop keeps the current fail-closed behavior.

## 8. Network, timeout, and payload limits (DEC-UD-2, ratified)

### 8.1 Request limits

- **Exactly one URL**, constant and auditable: `https://pypi.org/pypi/castlearq/json`. HTTPS only; no scheme downgrade; no user-configurable URL; no URLs extracted from a previous response are ever fetched (no download links, no `project_urls` traversal, no GitHub).
- **Redirects:** allowed only when the final URL host is `pypi.org`; any off-host redirect is a failure (implemented by validating the final URL or a refusing redirect handler).
- **Total deadline:** `MAX_UPDATE_CHECK_TIMEOUT_SECONDS = 10.0`, measured **once per user trigger and covering the complete operation** — the PyPI request, every read chunk, and JSON decoding. The deadline is absolute: it never restarts or extends during the read, so a slow-drip response cannot keep the operation alive indefinitely. The installed-version probe keeps its own existing 5 s bound and must finish inside the same total budget.
- **Zero automatic retries** (mirroring the DEC-E no-retries posture): one attempt per trigger.
- **User agent:** a fixed descriptive string identifying CastleArq Desktop; no other custom headers.
- **No UI blocking:** the whole operation runs asynchronously off the GUI thread / on the Qt event loop (§10).

### 8.2 Response limits and schema validation

- **Size cap:** `MAX_UPDATE_RESPONSE_BYTES = 2 MiB` (2 × 1024 × 1024 bytes) of response body. The cap bounds the memory Desktop will buffer for one response; PyPI project JSON includes every historical release-file entry, so the existing 64 KiB health-body cap is intentionally not reused. Reading stops at the cap; a truncated body that fails validation yields `SEARCH_FAILED` (`RESPONSE_TOO_LARGE`).
- **Content checks, in order:** HTTP status == 200 → body decodes as UTF-8 JSON → top-level value is an object → `releases` exists and is an object → usable stable versions exist per §6.2. Any violation is `SEARCH_FAILED` with the specific reason (`HTTP_NON_SUCCESS`, `INVALID_JSON`, `INCOMPLETE_METADATA`, `NO_STABLE_VERSION`).
- **Yanked / withdrawn releases:** releases whose files are all flagged yanked/withdrawn, and entries with an empty file list, are excluded from the stable-selection pool — the documented policy is that "latest stable" means *installable and not withdrawn*. Excluded keys are recorded in technical context.
- **No trust in content:** response data is inert bytes. It is never executed, never imported, never written to disk, never used to build a command line, and never used as a URL to fetch.
- **Staleness:** a result arriving after the total deadline, or after supersession, is discarded without touching the UI (§7).

## 9. Local discovery errors (DEC-UD-3, ratified)

The future implementation defines a **discovery-local error type** (proposed name: `UpdateDiscoveryError`, following the `CastleArqError` conventions: stable machine-readable reason + actionable user-facing message + structured technical context). The global `ErrorCode` catalog of DEC-D is **not** modified, and no discovery condition is ever mapped into it.

| Reason | Condition | User-facing posture |
| --- | --- | --- |
| `NETWORK_ERROR` | DNS/connection/TLS failure | The service could not be reached; an existing, working Core keeps working normally |
| `TIMEOUT` | The §8 total deadline (10 s) expired before the operation completed | The check took too long and was abandoned; nothing is implied about versions |
| `HTTP_NON_SUCCESS` | Status other than 200 | The index answered unexpectedly; try again later |
| `INVALID_JSON` | Body is not valid UTF-8 JSON | The index response could not be understood |
| `INCOMPLETE_METADATA` | Required fields absent/wrong-typed (e.g., `releases` missing or not an object) | The index response is incomplete |
| `NO_STABLE_VERSION` | No parseable, non-pre-release, installable, non-withdrawn version | No valid stable release could be identified |
| `RESPONSE_TOO_LARGE` | Body exceeded the 2 MiB cap | The index response was too large to trust |
| `INSTALLED_UNKNOWN` | No interpreter configured, probe failed, or the probe reported an invalid version | Published information (when available) is still shown, with the installed version marked unknown |

Cross-cutting rules:

- `INSTALLED_UNKNOWN` is reported alongside any PyPI-side outcome; it never masks a successful PyPI result, and a PyPI-side failure never masks an unknown installed version — both facts can be shown together.
- PyPI-side failures resolve to the `SEARCH_FAILED` state (§7) with exactly one primary reason from the table; installed-side problems resolve to the `INSTALLED_UNKNOWN` state.
- No stack trace, raw body, or exception text is ever a primary user-facing message; it lives only in structured technical context.
- A failed or successful discovery **cannot** affect launch, readiness, shutdown, or any other subsystem; it shares no state with them — a working Core stays usable when PyPI is unavailable.

## 10. Non-blocking behavior

- The PyPI request runs asynchronously on the Qt event loop via `QNetworkAccessManager` (the established pattern from `readiness_qt_transport.py`), with a `QTimer`-driven **absolute** deadline (§8); no threads are introduced by the feature itself, and the GUI thread never performs a synchronous network call.
- The installed-version probe is a bounded subprocess; it must run off the GUI thread or through the existing injectable-execution seam (analogous to the coordinator's `version_executor`) so the UI stays responsive.
- Core remains fully usable during a check: starting, readiness, and shutdown are unaffected, and closing the application cancels the in-flight request (a cancelled discovery is inert — it never becomes a terminal state surfaced as success).
- Results that arrive after the total deadline (§8) or after the operation was superseded are dropped without touching the UI (DEC-UD-2); both cancellation and staleness are inert.

## 11. Interface and concurrency (DEC-UD-4, ratified)

- **Location:** the check lives in the application settings UI, under **Settings → Updates** (a dedicated section of the existing settings surface). It is not placed in the startup path or the main status area.
- **Manual only:** the sole trigger is an explicit user action inside that section. No timers, no start-up checks, no scheduled or background checks exist in v1.
- **At most one active query:** while a query is in flight (`IN_PROGRESS`), a second trigger does **not** start a second request; the UI keeps showing the in-progress state of the current operation (e.g., the action disabled with a "searching" indicator).
- **Interpreter optional:** a configured interpreter is **not** required to query PyPI. Without one — or when the installed version cannot be determined — the published information is still displayed when available, with the installed version explicitly marked unknown (§7 `INSTALLED_UNKNOWN`, §9).
- **Result presentation:** the UI always shows the three quantities separately (installed, latest published stable, locally admitted). A published-but-not-admitted version is never offered as a compatible update and never triggers any action beyond the informational display.
- **No environment mutation:** the section installs nothing, downloads no distribution, and modifies no Python environment; it exposes no "Update" or "Install" action in v1.

## 12. Acceptance criteria (for a future implementation)

1. The check executes only after an explicit user action from Settings → Updates; no code path triggers it automatically (verifiable by inspection: no timers, no start-up call).
2. Exactly one HTTPS GET to the fixed `pypi.org` JSON endpoint occurs per trigger; no other network destination is contacted; zero automatic retries occur on any failure.
3. The complete operation respects the single 10 s total deadline (request + reads + decode, no deadline restarts) and the 2 MiB response cap; exceeding either yields the corresponding failure, never a hung UI.
4. The installed version shown always comes from `query_installed_core_version()` with the explicitly selected interpreter path; with no selection or an unresolved probe, `INSTALLED_UNKNOWN` is reported while published data is still shown when available.
5. Version parsing, ordering, and pre-release detection use the declared `packaging` runtime dependency (DEC-UD-1); no hand-written comparator exists; invalid version strings are treated as unknown, never as comparable versions.
6. Given PyPI reports a newer stable `x` and the allowlist snapshot lacks `x`, the terminal state is `NEWER_STABLE_NOT_ADMITTED` and the UI copy never calls `x` a compatible update.
7. Given `x` is both newer than installed and in the allowlist snapshot, the state is `NEWER_STABLE_ADMITTED`; this changes nothing about admission behavior — READY still requires the real DEC-A path.
8. Pre-release-only newer versions never produce an update state in v1; yanked or file-less releases are excluded per §8.2.
9. Each failure mode of §9 deterministically yields its stable reason within the bounded time, with no stack trace in user-facing text.
10. While one query is active, a second trigger starts no second request and the UI keeps showing the current operation; a late or superseded result never updates the UI.
11. The allowlist, DEC-A functions, coordinators, readiness transport, and the global `ErrorCode` set are byte-for-byte unchanged.
12. During a check (including a failing one), launch/readiness/shutdown of Core behave identically to a run without any check.
13. The result model always exposes installed, latest-stable, and admitted versions separately, including unknown markers where unknown.
14. The deterministic test suite performs no real network access (simulated responses only).

## 13. Implementation plan (staged; not authorized by this document)

Each stage lands independently with its own verification; no stage expands the feature toward a full updater.

### Stage 1 — Discovery service (core logic, no UI)

- Manual-trigger entry point performing the §6 flow against the fixed PyPI endpoint.
- Metadata parsing and schema validation (§8.2), including the documented stable-selection policy (exclude pre-releases, invalid keys, file-less and yanked/withdrawn releases).
- PEP 440 comparison via the declared `packaging` runtime dependency (DEC-UD-1; the dependency addition to `pyproject.toml` lands in this stage, with the minimum version chosen by the §6.2 rule).
- Independent determination of local admission from a `SUPPORTED_CORE_VERSIONS` snapshot — read-only, never written.
- Discovery-local error type and reasons (DEC-UD-3); the 10 s total deadline and 2 MiB cap (DEC-UD-2); zero retries; stale-result dropping.
- **Depends on:** nothing. **Done when:** the service classifies every §7 terminal state from injected inputs, respects every §8 limit, and shares no state with launch/readiness/shutdown.

### Stage 2 — Service tests (deterministic, no real network)

- Version comparison: stable vs. pre-release vs. invalid inputs (via `packaging`).
- Valid PyPI response; malformed JSON; unexpected schema (missing/mistyped `releases`).
- Timeout (total deadline), non-2xx HTTP, and a response body exceeding 2 MiB.
- Project with no valid stable versions; installed version unknown.
- Published newer version not admitted (`NEWER_STABLE_NOT_ADMITTED`) and the newer-but-admitted case.
- All network simulated with crafted responses; the deterministic suite contacts no real endpoint.
- **Depends on:** Stage 1. **Done when:** every acceptance criterion concerning service behavior (§12 items 2–9, 13–14) is exercised by passing tests.

### Stage 3 — Settings → Updates UI

- The "Updates" settings section: manual check action, in-progress indicator, the three-version result display, and error presentation per §9/§11.
- Single active operation: repeated triggers are inert while `IN_PROGRESS`; late or superseded results are dropped.
- Non-blocking on the Qt event loop; the in-flight request is cancelled on window/app close.
- No install/download action; no environment mutation; published-not-admitted copy stays informational.
- UI-level tests for concurrency (double trigger) and for late-result dropping.
- **Depends on:** Stages 1–2. **Done when:** acceptance criteria §12 items 1, 10, 12 hold and the section behaves per §11.

## 14. Deferred decisions (still open; ratified DEC-UD-1..4 are not reopened here)

- **Result caching / TTL** — v1 performs no persistence and no caching; any future caching design must not become background behavior.
- **Pre-release channel** — explicitly out of v1; needs its own scope and security review.
- **GitHub (or any non-PyPI) discovery** — explicitly out of v1; needs its own scope and security review.
- **Any form of assisted installation** — out of scope for discovery; would be a separate feature with its own security review; "admitted" never implies "installable by Desktop".
- **Exact `packaging` lower bound** — the *method* for choosing it is fixed by DEC-UD-1/§6.2; the concrete pin is recorded together with the Stage 1 dependency change rather than here.
