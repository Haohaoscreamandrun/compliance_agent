# Small-CL Task Breakdown (`develop` → PR to `main` per Component)

## Component 1: Environment, Docker, Guardrails & Architecture Docs

- [x] **CL 1.1 (`chore(config)`):** Add public-repo `.gitignore`, local `.env` contract, and `tasks/todo.md` tracker.
- [x] **CL 1.2 (`chore(docker)`):** Add hardened `docker-compose.yml` (localhost port bindings, mandatory secret checks, healthchecks, isolated volumes).
- [x] **CL 1.3 (`docs(arch)`):** Add `docs/intent/label-compliance-mvp.md` and `docs/decisions/ADR-001-system-decisions.md` (with rationales for forfeited choices).
- [x] **CL 1.4 (`docs(web-audit)`):** Amend `ADR-001`, `README.md`, and `docker-compose.yml` with Modern Web Guidance findings (`<svg viewBox="0 0 1000 1000">` overlay, `fetchpriority="high"`, `@container` queries, WCAG 2.1 AA bilingual rules).
- [ ] **Checkpoint / PR #1:** Review & merge `develop` → `main` for Component 1.

## Component 2: PostgreSQL `JSONB` Schema & On-Call MOA `EIR027` Client

- [x] **CL 2.0 (`chore(backend-scaffold)`):** Isolate backend packaging & container scaffold (`backend/pyproject.toml`, `backend/Dockerfile`, `backend/app/__init__.py`, `backend/app/db/__init__.py`).
- [x] **CL 2.1 (`feat(db)`):** Add `app/config.py`, `app/db/session.py`, SQLAlchemy 2.0 `JSONB` models (`MoaRegistry`, `Document`, `DocumentPage`, `DocumentClause`), and `tests/test_db_and_config.py`.
- [x] **CL 2.2 (`feat(moa-client)`):** Implement `app/services/moa_client.py` (on-call `PesticideData.aspx?$filter=...` + 4 sub-endpoints lookup with PostgreSQL snapshot upsert) + unit tests.
- [ ] **Checkpoint / PR #2:** Review & merge `develop` → `main` for Component 2.

## Component 3: Pre-Processing, Constrained Decoding Schemas & Prompts

- [x] **CL 3.1 (`feat(schemas)`):** Implement `GroundedField[T]` Chain-of-Grounding (`verbatim_text` → `value` → `page` → `box_2d`) and 4 extraction schemas (`ProductLabelExtraction`, `MsdsExtraction`, `NotarizedCertExtraction`, `OfficialLetterExtraction`) + schema tests.
- [x] **CL 3.2 (`feat(parser)`):** Implement `preprocessor.py` (`PyMuPDF` + `Pillow` WebP normalizer), domain prompts, and `parser.py` (interleaved page-image batching + Gemini constrained decoding).
- [ ] **Checkpoint / PR #3:** Review & merge `develop` → `main` for Component 3.

## Component 4: Deterministic Cascade Diff, Two-Phase Copilot & FastAPI Routes

- [x] **CL 4.1 (`feat(diff)`):** Implement `cascade_diff.py` across Old Label, New MSDS, MOA Registry, Change Certificate, and Official Letters + unit tests.
- [x] **CL 4.2 (`feat(copilot)`):** Implement the 4 agent tools (`tools.py`), `ComplianceAuditReport` schema, and Two-Phase Copilot (`copilot.py`) + unit tests.
- [x] **CL 4.3 (`feat(api)`):** Wire FastAPI endpoints (`routes_registry.py`, `routes_documents.py`, `routes_copilot.py`, `main.py`) + seed fixtures.
- [ ] **Checkpoint / PR #4:** Review & merge `develop` → `main` for Component 4.

## Component 5: Angular + Angular Material UI & `<svg viewBox="0 0 1000 1000">` BBox Viewer

- [x] **CL 5.0 (`chore(frontend-scaffold)`):** Isolate pure Angular CLI & Angular Material scaffold (`npx @angular/cli@19 new frontend` + `ng add @angular/material` + `nginx.conf`) with zero custom business logic.
- [x] **CL 5.1 (`feat(ui-core)`):** Add `color-scheme: light dark` & `lang="zh-Hant"` root config, TypeScript models (`compliance.models.ts`), and `ComplianceApiService`.
- [x] **CL 5.2 (`feat(ui-viewer)`):** Build `BboxSvgViewerComponent` (inline `<svg role="img" viewBox="0 0 1000 1000" preserveAspectRatio="none">` over `fetchpriority="high"` `.webp` `<img>` with `width_px`/`height_px` zero-CLS sizing and `contain: layout style paint`), `CopilotAuditPanelComponent` (`@container` queries, semantic `<caption>`/`<th scope="col|row">` checklist, `>=24x24px` native `<button>` citations, bilingual `lang` tags, `<progress>` state), and native `<dialog>.showModal()` upload modal.
- [ ] **Checkpoint / PR #5:** Review & merge `develop` → `main` for Component 5.

## Future Enhancements (Post-MVP)

- [ ] **APHIA Announcements & Chemical Assessments:** Add separate lookup/sync for `pesticide.aphia.gov.tw` official announcements (`農藥公告`), banned/restricted/carcinogen lists, and ACRI chemical risk assessments (not exposed in `EIR027`).
