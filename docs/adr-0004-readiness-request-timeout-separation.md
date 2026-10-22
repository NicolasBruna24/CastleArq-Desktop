# ADR 0004 — Readiness Request Timeout Separation (Desktop)

- **Status:** Ratified — technical closure of readiness timeout separation accepted by human review
- **Scope:** CastleArq Desktop readiness timing only; specifically `src/castlearq_desktop/core/contracts/deadline.py`, `src/castlearq_desktop/core/readiness_qt_transport.py`, and `src/castlearq_desktop/core/readiness_client.py`
- **Date:** 2026-10-21
- **Relates to:** `docs/adr-0001-core-launch-contract.md` (D4 — 20 s startup timeout, 250 ms polling interval), `docs/core-update-discovery-spec.md` (§5 conventions, §9 error style), `src/castlearq_desktop/core/http_client.py`
- **Decision basis:** The technical decision was taken during the work that produced this change; the implementation is applied and technically verified in this repository according to the verification reports available (see §4). Human review has formally accepted and ratified the limited technical closure of the readiness request timeout separation (10 s readiness cap versus 4 s generic HTTP cap under the 20 s global budget), based on the 65 passing focused unit tests and the retained integration evidence summary.
- **Authorization boundary:** This document records the timing decision and the evidence for the change already applied in this repository. It does **not** authorize any change to CastleArq Core, to the DEC-A admission allowlist (admitting Core 0.6.0 is not ratified here), to the error-code vocabulary, or to any Git operation. No commit, push, or branch operation has been performed.

## 1. Context

CastleArq Desktop performs readiness probes against the three approved endpoints (`GET /health`, `GET /v1/models`, `GET /v1/artifacts`) of the fixed loopback endpoint `http://127.0.0.1:8000`. ADR-0001 (D4) fixes the outer readiness budget at 20 seconds with a 250 ms polling interval.

The per-request timeout was carried by a single shared constant, `MAX_REQUEST_TIMEOUT_SECONDS = 4.0`, in `core/contracts/deadline.py`. That constant is the generic maximum for any single HTTP request in Desktop, and it is also the default of `core/http_client.py`.

With CastleArq Core 0.6.0, `GET /v1/artifacts` answers **HTTP 200** but takes approximately **5.5–5.8 seconds**. The 4-second per-request cap therefore cancelled the request before the response was received, and every readiness pass failed with a per-request timeout. The generic 4-second cap was the direct cause; the endpoint was healthy and the 20-second global budget was never the binding constraint.

Elevating the shared `MAX_REQUEST_TIMEOUT_SECONDS` would have widened the timeout of *every* HTTP client in Desktop, including `core/http_client.py` and `core/catalog_client.py`, for a reason that applies only to the readiness path. The correction therefore separates the two concerns rather than relaxing the shared bound.

## 2. Decision

### 2.1 Readiness owns a distinct per-request cap

A new constant is introduced in `core/contracts/deadline.py`:

```text
READINESS_REQUEST_TIMEOUT_SECONDS = 10.0
```

The generic maximum is **not** changed and remains `MAX_REQUEST_TIMEOUT_SECONDS = 4.0`. The two caps coexist, each with a single owner:

| Constant | Value | Owner |
| --- | --- | --- |
| `READINESS_REQUEST_TIMEOUT_SECONDS` | 10.0 s | Readiness probes only (Qt transport and the `urllib` fallback) |
| `MAX_REQUEST_TIMEOUT_SECONDS` | 4.0 s | Generic HTTP boundary, including `core/http_client.py` and `core/catalog_client.py` |

The 10-second value leaves roughly 4 seconds of headroom over the observed 5.5–5.8 second response of `GET /v1/artifacts` on Core 0.6.0.

### 2.2 The global deadline remains the outermost bound

Every readiness request is still started with an effective timeout of `min(cap, remaining global budget)`, computed through the existing `Deadline.effective_request_timeout(now, max_request_timeout=...)` seam. The global 20-second budget is therefore unchanged and remains the outermost limit: no single readiness request can outlive the budget, and a request is never started once the budget is exhausted.

The 10-second cap is strictly smaller than the 20-second global deadline, so the cap can never by itself exceed the whole budget.

### 2.3 Scope of the change

The readiness-specific cap is applied in both readiness paths:

- **Production Qt path** — `core/readiness_qt_transport.py`. The cap is exposed as an injectable constructor parameter `request_timeout` (default `READINESS_REQUEST_TIMEOUT_SECONDS`), so a test or a future policy revision can exercise another cap without touching the global budget.
- **Fallback path** — `core/readiness_client.py`, the retained synchronous `urllib` engine.

`core/http_client.py` is **unchanged**: its default timeout remains the generic 4-second maximum.

### 2.4 Preserved decisions

The following approved values are unchanged by this decision:

- `GLOBAL_DEADLINE_SECONDS = 20.0` (ADR-0001 D4).
- `MAX_REQUEST_TIMEOUT_SECONDS = 4.0` (generic HTTP boundary).
- `POLL_INTERVAL_SECONDS = 0.25` (ADR-0001 D4).
- `TRANSPORT_RETRIES_ENABLED = False` (no automatic transport retries).
- The DEC-A admission allowlist, the error-code vocabulary, and ADR-0001 D6 (no Core changes).

No new error code, status value, or contract is introduced.

## 3. Rationale (non-normative)

The failure was a mis-scoped bound, not a missing bound: readiness probes a slower endpoint than the generic HTTP boundary was sized for. Separating the caps keeps the generic boundary tight where it matters and relaxes it only where the evidence requires it, and it makes the relaxation auditable as a single named constant rather than as a change to a shared value.

Introducing a second cap required no new architecture: `Deadline.effective_request_timeout` already accepted an explicit `max_request_timeout` override, so the change is confined to a constant, an export, and the two call sites that perform readiness probes.

## 4. Evidence

### 4.1 Contrasting runs

Two contrasting sets of runs were executed against a real Core 0.6.0:

- With the 4-second per-request cap: readiness failed with a per-request timeout
  in **2 of 2** runs (baseline; elapsed 19.75 s and 20.10 s — the global budget was
  consumed by repeated 4-second polling timeouts).
- With the 10-second per-request cap: readiness reached `READY` in **3 of 3**
  runs.

Both sets were produced against an **isolated candidate** in which the shared
`MAX_REQUEST_TIMEOUT_SECONDS` was raised from 4.0 s to 10.0 s, leaving the
generic cap otherwise untouched. They therefore contrast the *size of the
per-request bound*. They are **not** a contrast on the final separation
mechanism, which was implemented afterwards (§4.2). The primary logs of these
six runs are retained (`baseline_4s.log`, `baseline_4s_run2.log`,
`candidate_10s_run1..3.log`).

### 4.2 Final implementation

The definitive implementation in this repository (the code under review, not a prototype) reached `READY` in **3 of 3** consecutive runs against a real CastleArq Core 0.6.0 installation, using the production Qt readiness path:

| Run | Readiness cap | Generic cap | Outcome | Elapsed |
| --- | --- | --- | --- | --- |
| 1 | 10.0 s | 4.0 s | `READY` (`passes=2`, `error_code=None`) | 6.279 s |
| 2 | 10.0 s | 4.0 s | `READY` (`passes=2`, `error_code=None`) | 6.012 s |
| 3 | 10.0 s | 4.0 s | `READY` (`passes=2`, `error_code=None`) | 6.068 s |

Per-request durations observed in those runs: `GET /health` ≈ 22 ms, `GET /v1/models` ≈ 3 ms, `GET /v1/artifacts` ≈ 5.66 s (HTTP 200). The Core child remained alive throughout, and shutdown completed cleanly.

#### 4.2.1 Traceability of this evidence — known limitation

The results in the table above are **not** reproduced here from a preserved
primary log. The following is the exact state of the retained evidence:

- The three runs in the table were executed with the real repository code
  (`CAND_SRC=/home/brunapc/CastleArq-Desktop/src`) against a real Core 0.6.0.
  They are **documented in the retained experiment manifest** (`MANIFEST.md` in
  the scratch directory of the session that produced them), which records the
  three `READY` outcomes, the elapsed times, and the per-request durations.
- The **primary logs of those three final runs are not available** for an
  independent reproduction. The figures in the table and in the manifest are a
  **summary of that execution**, retained as a report.
- The primary logs that *are* retained (`baseline_4s.log`,
  `baseline_4s_run2.log`, `candidate_10s_run1..3.log`) belong to the
  **isolated-candidate experiment** described in §4.1, whose elapsed times
  (6.24 / 6.06 / 6.34 s) and per-request durations (5698 / 5668 / 5773 ms)
  differ slightly from the final-integration figures above. They corroborate the
  same qualitative result — the 4-second per-request bound times out while the
  endpoint answers HTTP 200 in ~5.5–5.8 s, and a 10-second bound yields
  `READY` — but they are **not** the logs of the runs in the table.

The evidence therefore supports the reported result, but its traceability has
this limitation: the final-integration numbers cannot be independently
re-derived from the retained primary logs. Reproducing them would require
re-running the integration against a real Core 0.6.0, which is outside the
scope of this decision and was not performed for this revision.

### 4.3 Unit tests

The deterministic suite covers the separation explicitly:

- `tests/test_contract_deadline.py` — the readiness cap is 10.0 s, the generic maximum is unchanged at 4.0 s, the readiness cap is strictly below the global deadline, the effective timeout remains `min(cap, remaining budget)` (including the exhausted-budget case), and the `core/http_client.py` default is still the generic 4 s.
- `tests/test_readiness_client.py` — the Qt transport arms its per-request timers with the readiness cap and not with the generic maximum, the fallback `urllib` path uses the readiness cap, the cap is injectable, and an injected cap still respects the remaining global budget.

## 5. Limitations

- The integration evidence is **local**: it was produced against a locally installed CastleArq Core 0.6.0 in a test environment, not in a production deployment. **No production deployment is claimed.**
- No concurrent-load testing was performed. The observed 5.5–5.8 second latency of `GET /v1/artifacts` was measured on an idle local Core; slower environments are not covered.
- The 10-second cap is evidence-based for the observed endpoint latency, not a universal guarantee. An environment in which `GET /v1/artifacts` exceeds 10 seconds would still fail readiness, and would require a separately authorized decision.
- The margin between the 10-second cap and the observed ~5.8 second latency is roughly 4 seconds and has not been characterised statistically across many runs.
- DEC-A admission remains fail-closed and unchanged. Reaching `READY` in the runs above relied on the versions in the local allowlist being admitted; this decision does not change which Core versions are admitted.

## 6. Relationship to existing governance

- `docs/adr-0001-core-launch-contract.md` is unchanged. Its D4 requirements (20 s startup timeout, 250 ms polling interval) remain in force and are unaffected.
- `docs/adr-0002-application-exit-coordination.md` and `docs/adr-0003-pending-startup-observation.md` are unchanged.
- `docs/core-update-discovery-spec.md` is updated only in its two cross-references to the readiness timing convention (its own decisions are untouched).
- `README.md` is updated only in the sentence that describes the readiness timing.
- **DEC-E has no independent document in this repository.** Neither the current
  tree nor the repository history contains a standalone DEC-E file. The label
  "DEC-E" survives only as a historical tag inside code docstrings (in
  `core/contracts/deadline.py`, `core/readiness_qt_transport.py`,
  `core/readiness_client.py`, `core/http_client.py`, and
  `core/startup_coordinator.py`) and no longer appears in any Markdown
  document. Because no DEC-E document exists, **no normative content can be
  attributed to it from the repository itself**; this ADR therefore neither
  cites DEC-E as a normative source nor extends it. The normatively binding
  requirements for the readiness timing are ADR-0001 D4 (20 s startup timeout,
  250 ms polling interval) and `src/castlearq_desktop/core/contracts/deadline.py`.
  Whether the historical DEC-E label was intended to denote exactly that
  contract cannot be established from the repository alone and is therefore not
  asserted here.

## 7. Non-goals and authorization boundary

This decision does not authorize: any change to CastleArq Core; any change to the DEC-A admission allowlist; any new error code, contract, or vocabulary; any change to the shutdown contract; any dependency change; any Git operation (no commit, push, or branch change has been made); or any deployment.

The three standard suite failures currently observed in the working tree are **not** regressions caused by this decision and do not block it: two concern the unratified working tree modification to the DEC-A admission allowlist (`{0.5.1, 0.6.0}` in `version_admission.py` versus `{0.5.1}` expected by the architectural isolation tests) and one concerns `_request_start_times` and `_request_durations` being instance attributes where a newly added test in `test_lifecycle_manager.py` checks them on the class. None of these three failures existed in `HEAD`. The allowlist discrepancy and the diagnostic logging attribute-shape issue are **separate concerns** belonging to other pending workstreams and are not merged into a single item. See `docs/readiness-timeout-closure-record.md`.

The three failures above are the failures of the **standard suite run** recorded in
the closure report. They are not a claim that no other failure exists under the
optional opt-in integration gate (`CASTLEARQ_RUNTIME_IT=1`), which was **not
executed** for this decision and which is excluded from those counts.
