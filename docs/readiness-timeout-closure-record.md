# CastleArq Desktop — Readiness Timeout Correction Closure Record

**Status:** RATIFIED — LIMITED TECHNICAL CLOSURE ACCEPTED BY HUMAN REVIEW

**Decision basis:** The implementation is applied in this repository and is
technically verified according to the verification reports available (the
numbers in §2). **Human review has formally accepted and ratified the limited
technical closure of the readiness request timeout separation**: this status
records that the separation of the 10 s readiness cap from the generic 4 s cap
under the 20 s global budget is accepted as technically resolved, without
ratifying the admission of Core 0.6.0 under DEC-A or the diagnostic logging
instrumentation test shape.

This file records the completed technical work of the readiness timeout
correction for CastleArq Core 0.6.0. It is a verification record, not an
architectural decision record: the decision itself is recorded in
`docs/adr-0004-readiness-request-timeout-separation.md`.

## 1. Scope completed

Code files modified by this correction:

- `src/castlearq_desktop/core/contracts/deadline.py` — new
  `READINESS_REQUEST_TIMEOUT_SECONDS = 10.0` constant and its export.
- `src/castlearq_desktop/core/contracts/__init__.py` — re-export of the new
  constant.
- `src/castlearq_desktop/core/readiness_qt_transport.py` — production Qt path:
  the per-request timeout is now the readiness cap, exposed as the injectable
  `request_timeout` constructor parameter.
- `src/castlearq_desktop/core/readiness_client.py` — fallback `urllib` path:
  the per-request timeout is now the readiness cap.
- `src/castlearq_desktop/core/startup_coordinator.py` — docstring only.

Test files modified:

- `tests/test_contract_deadline.py`
- `tests/test_readiness_client.py`

Documentation files modified:

- `docs/adr-0004-readiness-request-timeout-separation.md` (new; the decision).
- `docs/core-update-discovery-spec.md` — two cross-references to the readiness
  timing convention only; its own decisions are untouched.
- `README.md` — the sentence describing the readiness timing only.

Approved values and their state after the correction:

- `READINESS_REQUEST_TIMEOUT_SECONDS = 10.0` (new, readiness only).
- `MAX_REQUEST_TIMEOUT_SECONDS = 4.0` (unchanged, generic HTTP boundary).
- `GLOBAL_DEADLINE_SECONDS = 20.0` (unchanged).
- `POLL_INTERVAL_SECONDS = 0.25` (unchanged).
- `TRANSPORT_RETRIES_ENABLED = False` (unchanged).
- `core/http_client.py` — **not modified**; its default remains the generic
  4-second maximum.
- `core/catalog_client.py` — **not modified**.
- CastleArq Core — **not accessed and not modified**.
- DEC-A admission allowlist — **not modified**.
- The effective readiness timeout remains `min(cap, remaining global budget)`.

## 2. Verification evidence

### 2.1 Real-Core integration

Against a real, locally installed CastleArq Core 0.6.0 through the production
Qt readiness path, the final implementation reached `READY` in **3 of 3**
consecutive runs:

| Run | Outcome | Passes | Error code | Elapsed |
| --- | --- | --- | --- | --- |
| 1 | `READY` | 2 | `None` | 6.279 s |
| 2 | `READY` | 2 | `None` | 6.012 s |
| 3 | `READY` | 2 | `None` | 6.068 s |

Observed per-request durations: `GET /health` ≈ 22 ms, `GET /v1/models` ≈ 3 ms,
`GET /v1/artifacts` ≈ 5.66 s with HTTP 200. The Core child stayed alive and
shutdown completed cleanly in every run.

Contrasting runs with the previous 4-second cap: readiness failed with a
per-request timeout in **2 of 2** runs. Both contrasting sets were produced
against an **isolated candidate** whose shared `MAX_REQUEST_TIMEOUT_SECONDS` was
raised from 4.0 s to 10.0 s, so they contrast the *size of the per-request
bound*, not the final separation mechanism.

**Traceability limitation.** The three final runs above were executed with the
real repository code against a real Core 0.6.0 and are **documented in the
retained experiment manifest** (`MANIFEST.md`), which records the three `READY`
outcomes, the elapsed times, and the per-request durations. The **primary logs
of those three final runs are not available** for an independent reproduction,
so the figures in the table are a retained summary of that execution rather
than a re-derivable artifact. The primary logs that *are* retained
(`baseline_4s.log`, `baseline_4s_run2.log`, `candidate_10s_run1..3.log`) belong
to the isolated-candidate experiment described above, with elapsed times of
6.24 / 6.06 / 6.34 s, and corroborate the same qualitative outcome without
being the logs of the runs in the table. The evidence therefore supports the
reported result, with this limitation on its independent traceability.
Reproducing it would require re-running the integration against a real Core
0.6.0, which was not performed for this revision.

This evidence is **local**. It is not a production deployment, and no
production deployment is claimed.

### 2.2 Unit and contract tests

The focused suite covering the changed contracts and both readiness paths
passes: **65 passed**, 0 skipped, with `PySide6` available in the project
virtualenv (`.venv`, PySide6 6.12.0). The new assertions cover the separation
of the two timeouts (readiness 10 s versus generic 4 s), the respect of the
remaining global budget, the injectability of the readiness cap, and the
unchanged `core/http_client.py` default.

### 2.3 Full suite

The complete suite was executed during the review with the project
virtualenv: **3 failed, 756 passed, 9 skipped** (22.42 s). None of these three
failures existed in `HEAD` (where `SUPPORTED_CORE_VERSIONS` was `{"0.5.1"}` and
the instrumentation test did not exist); they stem from independent working tree
modifications outside the readiness timeout separation scope, as recorded in §4:

- `tests/test_lifecycle_manager.py::test_readiness_transport_has_instrumentation_attributes`
- `tests/test_update_check_ui.py::test_controller_neutrality`
- `tests/test_update_discovery.py::test_allowlist_still_only_051_and_result_snapshot_is_defensive`

They are not regressions of the timeout separation. Focused deadline and
readiness unit tests (65 tests) pass with zero failures. They are recorded in §4
and are not blocking this timeout correction. No new failure was introduced by
the timeout separation.

**Scope of this count.** The three failures above are the failures of the
**standard suite run**. They are not a claim that no other failure exists under
the optional opt-in integration gate (`CASTLEARQ_RUNTIME_IT=1` in
`tests/test_runtime_integration.py`), which was **not executed** for this
correction and which is excluded from these counts. The retained experiment
manifest records that the same allowlist discrepancy also affects that gate,
which is a further reason not to read "3" as a complete inventory of possible
failures. Neither that gate nor any expectation under it was changed.

## 3. Repository and Git limitations

- `git status --short` shows a working tree with both the files modified by
  this correction and files modified by other pending working tree workstreams
  (`pyproject.toml`, `src/castlearq_desktop/app.py`, `core/lifecycle_manager.py`,
  `core/subprocess_adapter.py`, `core/version_admission.py`,
  `ui/main_window.py`, `ui/model_explorer_view.py`, and the corresponding
  tests). Those other changes were left untouched.
- The `readiness_qt_transport.py` diff mixes this correction with pending
  diagnostic-logging instrumentation (the `logging` import, the
  `_request_start_times` / `_request_durations` attributes, `_log_pass`, and the
  `READINESS FINAL` debug lines). Neither was removed: the logging
  instrumentation is out of scope for this correction and the
  `_request_start_times` attribute is referenced by the working tree test
  discrepancy described in §4.
- No commit, push, branch change, `git reset`, `git clean`, `git checkout`, or
  `git stash` has been performed. The working tree was not made artificially
  clean.

## 4. Working tree test discrepancies outside readiness timeout scope (not blocking, not modified)

These three failures are not regressions of the readiness timeout separation and
did not exist in `HEAD`. No test file or expectation was modified to address them.

1. **Two failures — DEC-A allowlist modification:** The working tree edits
   `SUPPORTED_CORE_VERSIONS` to `{"0.5.1", "0.6.0"}` in `version_admission.py`,
   which contradicts the architectural isolation tests (`test_controller_neutrality`
   and `test_allowlist_still_only_051_and_result_snapshot_is_defensive`) that assert
   the ratified `{"0.5.1"}` policy. Reconciling the two requires an explicit
   architectural ratification about admitting Core 0.6.0 and is explicitly
   **out of scope** for this readiness timeout correction.
2. **One failure — Instrumentation test shape mismatch:** A newly added test in
   `tests/test_lifecycle_manager.py` checks for `_request_start_times` and
   `_request_durations` on the `QtReadinessTransport` class, whereas the
   diagnostic-logging instrumentation defines them as instance attributes in
   `__init__`. This belongs to the diagnostic-logging workstream, not to the
   timeout separation.

No new failure was introduced by the readiness timeout correction.

## 5. Governance and next steps

- The decision is recorded in `docs/adr-0004-readiness-request-timeout-separation.md`.
- `docs/adr-0001-core-launch-contract.md`, `docs/adr-0002-application-exit-coordination.md`,
  and `docs/adr-0003-pending-startup-observation.md` are unchanged.
- **DEC-E has no independent document** in this repository, either in the
  current tree or in its history. The label survives only as a historical tag
  in code docstrings. No normative content is attributed to it here, and the
  normative readiness-timing requirements remain ADR-0001 D4 and
  `core/contracts/deadline.py`. See §6 of the ADR-0004 for the full statement.
- The reconciliation of the DEC-A allowlist with the existing tests is a
  separate decision and remains open.
- The reconciliation of the `_request_start_times` attribute shape with its
  test is likewise a separate concern from the allowlist item.
- No Git operation is authorized or performed.
- This limited technical correction has been accepted and ratified by human
  review for the readiness timeout separation scope. Admission of Core 0.6.0
  under DEC-A and the resolution of the diagnostic-logging instrumentation
  tests remain separate pending decisions.
