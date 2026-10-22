# ADR 0005 — CastleArq Core Version Admission Policy (DEC-A)

- **Status:** Ratified — decision to admit CastleArq Core 0.5.1 and 0.6.0 accepted by human review
- **Scope:** CastleArq Desktop Core version admission policy only; specifically `src/castlearq_desktop/core/version_admission.py` and its architectural isolation invariants
- **Date:** 2026-10-22
- **Relates to:** `docs/adr-0001-core-launch-contract.md` (DEC-A — conservative version admission), `docs/adr-0004-readiness-request-timeout-separation.md` (readiness timing covering Core 0.6.0 probe latencies), `src/castlearq_desktop/core/version_admission.py`
- **Decision basis:** The technical recommendation is based on a read-only contractual audit of CastleArq Core 0.6.0 (`/home/brunapc/LocalAI Hub`) against Desktop's requirements (`GET /health`, `GET /v1/models`, `GET /v1/artifacts`, and process launch). Human review has formally accepted and ratified the expansion of the DEC-A allowlist to admit both `0.5.1` and `0.6.0`.
- **Authorization boundary:** This document records the architectural decision to admit Core 0.5.1 and 0.6.0. It does **not** authorize any change to CastleArq Core, any dependency change, any Git operation (no commit, push, or branch change), nor does it claim that the subsequent test alignment and documentation update phases have been executed.

---

## 1. Context

Desktop does not bundle CastleArq Core; it launches and connects to a separately installed Core distribution located in an interpreter explicitly chosen by the user. Under the DEC-A architectural policy, admission of Core versions is conservative and **fail-closed**:

1. A candidate Core version is admitted only if it matches a string in the explicit allowlist `SUPPORTED_CORE_VERSIONS`.
2. Ranges, wildcards, and semver-based automatic promotions (e.g. `0.6.*` or `>=0.5.1`) are forbidden.
3. Every readiness pass verifies that the distribution version reported by the isolated probe (`importlib.metadata.version("castlearq")`) matches the top-level `"version"` field in the `GET /health` JSON payload exactly.

Previously, DEC-A admitted exactly one version: `frozenset({"0.5.1"})`. With the availability of CastleArq Core 0.6.0, Desktop required evaluation to determine whether Core 0.6.0 satisfies all required integration contracts without breaking backward compatibility with Core 0.5.1.

---

## 2. Decision

### 2.1 Explicit dual-version allowlist

The allowlist is updated to explicitly admit both CastleArq Core 0.5.1 and 0.6.0:

```python
SUPPORTED_CORE_VERSIONS: FrozenSet[str] = frozenset({"0.5.1", "0.6.0"})
```

The candidate reference version for validation and development testing is established as:

```python
CANDIDATE_VERSION: str = "0.6.0"
```

### 2.2 Invariant rules preserved

- **Exact string matching:** Versions remain exact strings without ranges or wildcards.
- **Fail-closed posture:** If Core reports any version other than `"0.5.1"` or `"0.6.0"`, the admission evaluation yields `VersionAdmission.UNKNOWN` and readiness immediately aborts with `ErrorCode.API_CONTRACT_INCOMPATIBLE`.
- **Strict identity agreement:** If the installed distribution version disagrees with the runtime `/health` payload version, the state is classified as `VersionAdmission.INCOMPATIBLE` and startup is refused.

---

## 3. Contractual Evidence

A read-only contractual audit of CastleArq Core 0.6.0 verified the following interfaces:

1. **Process launch:** Core 0.6.0 supports execution via `[<python>, "-m", "castlearq.main", "serve"]` on `127.0.0.1:8000` with stdio redirection to `/dev/null`.
2. **Metadata probe:** The standard library probe `importlib.metadata.version("castlearq")` returns `"0.6.0"`, consistent with `pyproject.toml`.
3. **Endpoint `GET /health`:** Implemented in `castlearq/api.py` (L981). Returns HTTP 200 with Content-Type `application/json; charset=utf-8` and payload `{"status": "ok", "version": "0.6.0"}`.
4. **Endpoint `GET /v1/models`:** Implemented in `castlearq/api.py` (L983). Returns HTTP 200 with dictionary `{"models": [...]}` containing the expected model DTO fields.
5. **Endpoint `GET /v1/artifacts`:** Implemented in `castlearq/api.py` (L986). Returns HTTP 200 with dictionary `{"artifacts": [...]}`. The observed response latency of ~5.5–5.8 seconds is accommodated by the ratified 10-second readiness request cap in ADR-0004 (`READINESS_REQUEST_TIMEOUT_SECONDS`).

---

## 4. Evidence Limitations

- **Contractual verification versus execution validation:** The evidence gathered for this decision consists of a static and structural code audit of the Core 0.6.0 codebase and test suite, combined with the retained integration evidence recorded in `docs/readiness-timeout-closure-record.md`.
- **No new integration executed:** No new subprocess integration or live execution of Core was performed during the creation of this record.
- **Scope limitation:** This evaluation covers the endpoints and mechanisms required by Desktop's lifecycle, readiness, and model catalog interfaces. It does not validate unspecified or auxiliary features of Core.

---

## 5. Consequences and Next Steps

1. **Test alignment (Pending Phase 2):**
   - The working tree currently contains modifications in `src/castlearq_desktop/core/version_admission.py` and `tests/test_version_admission.py` reflecting `0.6.0`.
   - Architectural isolation tests in `tests/test_update_check_ui.py` (`test_controller_neutrality`) and `tests/test_update_discovery.py` (`test_allowlist_still_only_051_and_result_snapshot_is_defensive`) currently expect only `{"0.5.1"}`. Those tests must be explicitly updated in a subsequent, authorized phase to assert the ratified allowlist `frozenset({"0.5.1", "0.6.0"})`.
2. **Documentation alignment (Pending Phase 3):**
   - `README.md` must be updated to document that both Core 0.5.1 and 0.6.0 are admitted.
   - `docs/readiness-timeout-closure-record.md` must be updated to note that DEC-A admission for 0.6.0 is now ratified.

---

## 6. Non-goals and Authorization Boundary

This decision is strictly limited to the admission policy under DEC-A. It does **not** authorize or cover:

- Modifications to CastleArq Core.
- Changes to the readiness timeout budgets ratified under ADR-0004.
- Changes to the process lifecycle state machine or diagnostic logging instrumentation.
- Model Explorer view improvements or downloads.
- Desktop package versioning (`pyproject.toml`) or release procedures.
- Git operations (`git add`, `git commit`, `git push`, branch switching, or rebasing).
