# CastleArq Desktop — WP2B Closure Record

**Status:** TECHNICALLY VERIFIED — FORMAL CLOSURE RECORD

This file records the completed technical verification of WP2B
(Lifecycle Manager contract C1–C8). It does not authorize a successor
work package.

## 1. Scope completed

Files:

- `src/castlearq_desktop/core/lifecycle_manager.py`
- `tests/test_lifecycle_manager.py`

Approved C1–C8 scope:

- Eight lifecycle states (`NOT_STARTED`, `STARTING`, `RUNNING`,
  `STOPPING`, `STOPPED`, `START_FAILED`, `UNEXPECTED_EXIT`,
  `SHUTDOWN_FAILED`); public nine-code `ErrorCode` vocabulary unchanged.
- Launch-argument construction
  (`[python, "-m", "castlearq.main", "serve"]`) with absolute
  interpreter-path validation; `shell=False`; one spawn per accepted start.
- Injectable process-adapter and clock protocols.
- Definite (`ProcessSpawnError`) versus uncertain
  (`ProcessOwnershipUncertainError`, possibly with `partial_handle`)
  process-creation outcomes; sticky handle-less `ownership_unresolved`.
- Start refusal semantics, immediate post-adoption observation with
  `failed / UNEXPECTED_EXIT / error=None` on instant exit, and ownership
  retention (`discard_terminated_handle()` before retry).
- Nonblocking shutdown: single `terminate()`, one `kill()` only when
  2.0s <= elapsed < 5.0s in `GRACEFUL`, `SHUTDOWN_FAILED` at >= 5.0s
  with no further signalling; repeated stops coalesce.
- Conservative unknown-status handling: both probes failing yields
  `unknown`, never treated as exit; handle retained, no signalling,
  no budget exhaustion on unknown status.
- Shutdown-failure recording (`SHUTDOWN_FAILED` / `FAILED` /
  `shutdown_failed_reported=True`) and late-exit confirmation
  preserving the prior failure record.
- Deterministic tests using fake adapters, fake handles, and a fake
  clock — no real subprocesses, sockets, sleeps, or Core launches.

No concrete `subprocess`/`Popen` adapter and no GUI integration were
implemented.

## 2. Verification evidence

User-terminal evidence (not rerun by this documentation task):

- `python -m pytest tests/test_lifecycle_manager.py -v` — **30 passed**.
- `python -m pytest -q` — completed without visible failures in the
  supplied terminal output; some tests were skipped. The exact total
  and skip count were not established.
- `git diff --check` — no reported errors.
- Final READ-ONLY source review — no C1–C8 discrepancies identified
  in the report supplied by the user.

## 3. Repository and Git limitations

- `git status --short` showed `.gitignore`, `README.md`, `docs/`,
  `pyproject.toml`, `src/`, and `tests/` as untracked.
- `git log --oneline -3` reported that branch `main` has no commits yet.
- `git diff --cached --stat` was empty.
- Consequently, repository-wide history cannot establish which files
  predated WP2B. No claim is made that only the two WP2B files exist
  as changes.
- No staging, commit, or push has been performed.

## 4. Explicitly deferred work

Not implemented by WP2B and not authorized by this record:

- Concrete `subprocess`/`Popen` adapter.
- Background execution mechanism and lifecycle wiring.
- GUI integration.
- Application-exit cleanup strategy.
- Readiness/HTTP/TCP integration.

Application-exit cleanup remains a mandatory gate before Desktop may
launch CastleArq Core.

## 5. Governance and next steps

- This file records WP2B completion evidence; it is not an
  architectural decision record.
- `docs/adr-0001-core-launch-contract.md` is left unchanged.
- No successor work package is authorized or inferred by this record.
- Future work requires a separate explicit scope and authorization.
- No Git operation is authorized.
