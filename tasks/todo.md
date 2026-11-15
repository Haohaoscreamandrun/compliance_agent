# Small-CL Task Breakdown (`develop` → PR to `main` per Component)

## Component 1: Environment, Docker, Guardrails & Architecture Docs

- [x] **CL 1.1 (`chore(config)`):** Add public-repo `.gitignore`, local `.env` contract, and `tasks/todo.md` tracker.
- [x] **CL 1.2 (`chore(docker)`):** Add hardened `docker-compose.yml` (localhost port bindings, mandatory secret checks, healthchecks, isolated volumes).
- [x] **CL 1.3 (`docs(arch)`):** Add `docs/intent/label-compliance-mvp.md` and `docs/decisions/ADR-001-system-decisions.md` (with rationales for forfeited choices).
- [ ] **Checkpoint / PR #1:** Review & merge `develop` → `main` for Component 1.

## Component 2: PostgreSQL `JSONB` Schema & On-Call MOA `EIR027` Client

- [ ] **CL 2.1 (`feat(db)`):** Add FastAPI `pyproject.toml`, `app/config.py`, `app/db/session.py`, and SQLAlchemy 2.0 `JSONB` models (`MoaRegistry`, `Document`, `DocumentPage`, `DocumentClause`).
- [ ] **CL 2.2 (`feat(moa-client)`):** Implement `app/services/moa_client.py` (on-call `PesticideData.aspx?$filter=...` + 4 sub-endpoints lookup with PostgreSQL snapshot upsert) + unit tests.
- [ ] **Checkpoint / PR #2:** Review & merge `develop` → `main` for Component 2.

## Component 3: Pre-Processing, Constrained Decoding Schemas & Prompts

- [ ] **CL 3.1 (`feat(schemas)`):** Implement `GroundedField[T]` Chain-of-Grounding (`verbatim_text` → `value` → `page` → `box_2d`) and 4 extraction schemas (`ProductLabelExtraction`, `MsdsExtraction`, `NotarizedCertExtraction`, `OfficialLetterExtraction`) + schema tests.
- [ ] **CL 3.2 (`feat(parser)`):** Implement `preprocessor.py` (`PyMuPDF` + `Pillow` WebP normalizer), domain prompts, and `parser.py` (interleaved page-image batching + Gemini constrained decoding).
- [ ] **Checkpoint / PR #3:** Review & merge `develop` → `main` for Component 3.

## Component 4: Deterministic Cascade Diff, Two-Phase Copilot & FastAPI Routes

- [ ] **CL 4.1 (`feat(diff)`):** Implement `cascade_diff.py` across Old Label, New MSDS, MOA Registry, Change Certificate, and Official Letters + unit tests.
- [ ] **CL 4.2 (`feat(copilot)`):** Implement the 4 agent tools (`tools.py`), `ComplianceAuditReport` schema, and Two-Phase Copilot (`copilot.py`) + unit tests.
- [ ] **CL 4.3 (`feat(api)`):** Wire FastAPI endpoints (`routes_registry.py`, `routes_documents.py`, `routes_copilot.py`, `main.py`) + seed fixtures.
- [ ] **Checkpoint / PR #4:** Review & merge `develop` → `main` for Component 4.

## Component 5: Angular + Angular Material UI & `<canvas>` BBox Viewer

- [ ] **CL 5.1 (`feat(ui-core)`):** Scaffold Angular 19 + Angular Material app, TypeScript models (`compliance.models.ts`), and `ComplianceApiService`.
- [ ] **CL 5.2 (`feat(ui-canvas)`):** Build `BboxCanvasViewerComponent` (`[ymin, xmin, ymax, xmax]` `0–1000` `<canvas>` overlay renderer) and `CopilotAuditPanelComponent` (Section 1 Evidence + Section 2 Checklist `MatTable`).
- [ ] **Checkpoint / PR #5:** Review & merge `develop` → `main` for Component 5.
