# CastleArq Desktop — Phase 8 Global Audit Report (Read-Only)

**Status:** READ-ONLY AUDIT — NO CODE CHANGES AUTHORIZED BY THIS REPORT.
**Date:** 2026-10-24
**Scope:** Full application audit (functional + visual design) against the post-7D.6 codebase.
**Git HEAD:** `ba01c592319a1590e4eff4f01e8d56cdcee1fc9e` on `main`.

## 1. Executive Summary

CastleArq Desktop is a PySide6 application acting as control plane and diagnostic console for the CastleArq Core subprocess. Responsibilities: launching Core with a strict version contract, monitoring readiness via HTTP, exposing operational diagnostics, orchestrating model downloads, presenting Core's catalog and local library.

The codebase is **mature in contract design** (ADRs 0001–0005, WP2B closure, readiness-timeout correction) but **immature in visual design coherence**: no shared style layer, no QSS, no resource files, each view styles itself with default Qt widgets. Some sections (OpenVINO, Chat) are thin or placeholder-grade compared to Model Explorer and Runtime pages.

## 2. Functional Matrix

### 2.1 Core Lifecycle & Launch

| Capability | Location | State |
|---|---|---|
| Subprocess spawn | `core/lifecycle_manager.py` | Complete (C1–C8) |
| Version admission (DEC-A) | `core/version_admission.py` | Complete (allowlist {0.5.1}) |
| Version probe | `core/version_probe.py` | Complete |
| Readiness polling | `core/readiness_client.py`, `readiness_qt_transport.py` | Complete (10s/4s/20s) |
| Startup coordination | `core/startup_controller.py`, `startup_coordinator.py`, `lifecycle_observer.py` | Complete |
| Application exit coordination | `core/shutdown_controller.py` | Complete |
| Process manager | `core/process_manager.py` | Complete |

### 2.2 UI Pages

| Page | Index | State | Notes |
|---|---|---|---|
| Runtime / Diagnóstico | 4 | Audited (7D.6) | PID, exit code, shutdown label |
| Model Explorer | 1 | Complete | Catalog + local availability + download |
| Local Library | 2 | Complete | Core's own `list --json` |
| Chat | 3 | Thin | No streaming |
| OpenVINO | 5 | Minimal | 8 max tokens, single model |
| Updates (Settings) | 6 | Complete | Manual check, three-version display |

### 2.3 Services

All core services (chat, download, catalog client/controller/transport, OpenVINO, update discovery, interpreter validator) are **Complete**.

### 2.4 Test Coverage

~300+ tests across 30 files. All passing. Deterministic fakes, no real subprocesses in unit tests.

### 2.5 Known Technical Debt

1. DEC-A allowlist — experiments with 0.6.0 not ratified.
2. Diagnostic logging test shape mismatch (`_request_start_times` / `_request_durations`).
3. Update discovery implemented ahead of spec authorization (governance gap).
4. No QSS / no shared style layer.
5. No resource files (icons, branding).

## 3. Visual Diagnosis

### 3.1 Current State

Stock PySide6 widgets, no custom styling: no `.qss`, no `setStyleSheet()`, no `.qrc`. Every view wraps in `QGroupBox`. Default spacing, fonts, colors. No icons, branding, dark mode, progress indicators, empty-state designs.

### 3.2 Strengths

Clear information hierarchy, consistent QGroupBox usage, text-selectable diagnostic labels, word-wrapped status, consistent Spanish terminology.

### 3.3 Weaknesses

1. No visual identity (looks like default Qt example).
2. Inconsistent density (dense text-heavy vs. sparse views).
3. No status color coding (text only).
4. No dark mode handling.
5. No loading/progress indicators.
6. No empty-state design.
7. Inconsistent button labels (mixed language/verb forms).
8. No responsive layout.

### 3.4 Recommended Visual Direction

**"Diagnostic Console"** — clean, information-dense, professional tool aesthetic (VS Code, JetBrains, Docker Desktop inspired).

- Dark-first with light fallback.
- Monospace for data, sans-serif for chrome.
- Color as signal: green=ready/verified, amber=pending, red=failed, blue=info.
- Compact density: 4px grid, 8px section, 12px card padding.
- Subtle 1px borders, no shadows.
- Icon + text labels on buttons.

### 3.5 Proposed Shared Style Layer

`ui/styles.py` defining color tokens, typography scale, spacing constants, global QSS (applied in `app.py`), and reusable widget helpers (`make_status_label`, `make_section_box`, `make_action_button`).

### 3.6 Validation Strategy

Apply style layer to one representative screen first: **Runtime / Diagnóstico** (most recently audited, clearest structure), then roll out.

## 4. Prioritized Next Initiatives

### P0 — Immediate

| # | Initiative | Rationale | Effort |
|---|---|---|---|
| 1 | Establish shared visual style layer (`ui/styles.py` + QSS) | Foundation; no risk to functional code | Medium |
| 2 | Validate style layer on Runtime page | Proves approach on most-audited screen | Small |
| 3 | Add status color coding (ready/failed/pending) | Biggest UX improvement for diagnostic tool | Small |

### P1 — Short Term

| # | Initiative | Rationale | Effort |
|---|---|---|---|
| 4 | Resolve DEC-A allowlist governance | Working tree has 0.6.0 experiments; tests assert 0.5.1 only | Governance |
| 5 | Fix diagnostic logging test shape | `_request_start_times` / `_request_durations` mismatch | Small |
| 6 | Add loading/progress indicators | Better feedback for async ops | Medium |
| 7 | Standardize button labels | Mixed language/verb forms | Small |

### P2 — Medium Term

| # | Initiative | Rationale | Effort |
|---|---|---|---|
| 8 | Enrich OpenVINO view | Minimal (8 max tokens, single model) | Medium |
| 9 | Enrich Chat view | No streaming, no markdown rendering | Large |
| 10 | Add empty-state designs | Better UX for empty lists | Small |
| 11 | Dark mode support | Explicit handling beyond Qt defaults | Medium |

### P3 — Long Term

| # | Initiative | Rationale | Effort |
|---|---|---|---|
| 12 | Icon set / branding | Requires design assets | Large |
| 13 | Responsive layout | Adapt to narrow windows | Large |
| 14 | Reconcile update discovery spec vs. implementation | Spec says "not authorized" but code exists | Governance |

## 5. Architecture Observations

### 5.1 Strengths

- Strict separation of concerns (UI thin, core owns logic).
- Contract-driven development (ADRs, closure records, specs).
- Deterministic testing (fake adapters, no real subprocesses).
- Fail-closed posture (admission, readiness, discovery).
- Public interface discipline (views read public properties).

### 5.2 Risks

- Governance gaps (update discovery ahead of spec; DEC-A experiments).
- View code duplication (`store_state_text` duplicated across views).
- No shared UI base class (each view independently wraps in QGroupBox).
- Sparse views (OpenVINO, Chat less polished than Model Explorer, Runtime).

## 6. Conclusion

The application's **functional core is solid**: lifecycle management, readiness, version admission, and the Model Explorer / Local Library / Runtime pages are well-engineered with strong test coverage and clear contracts. The main gap is **visual design coherence** — no shared style layer, no branding, no status color coding. Recommended next step: establish a shared visual style layer and validate it on the Runtime page, then incrementally roll out to all screens.

Governance gaps (DEC-A allowlist, update discovery authorization, diagnostic logging test shape) should be resolved through explicit human ratification before further implementation.
