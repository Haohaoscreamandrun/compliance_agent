# Small-CL Task Breakdown (`develop` → PR to `main` per Component)

## Component 1: Environment, Docker, Guardrails & Architecture Docs

- [x] **CL 1.1 (`chore(config)`):** Add public-repo `.gitignore`, local `.env` contract, and `tasks/todo.md` tracker.
- [x] **CL 1.2 (`chore(docker)`):** Add hardened `docker-compose.yml` (localhost port bindings, mandatory secret checks, healthchecks, isolated volumes).
- [x] **CL 1.3 (`docs(arch)`):** Add `docs/intent/label-compliance-mvp.md` and `docs/decisions/ADR-001-system-decisions.md` (with rationales for forfeited choices).
- [x] **CL 1.4 (`docs(web-audit)`):** Amend `ADR-001`, `README.md`, and `docker-compose.yml` with Modern Web Guidance findings (`<svg viewBox="0 0 1000 1000">` overlay, `fetchpriority="high"`, `@container` queries, WCAG 2.1 AA bilingual rules).
- [x] **Checkpoint / PR #1:** Review & merge `develop` → `main` for Component 1.

## Component 2: PostgreSQL `JSONB` Schema & On-Call MOA `EIR027` Client

- [x] **CL 2.0 (`chore(backend-scaffold)`):** Isolate backend packaging & container scaffold (`backend/pyproject.toml`, `backend/Dockerfile`, `backend/app/__init__.py`, `backend/app/db/__init__.py`).
- [x] **CL 2.1 (`feat(db)`):** Add `app/config.py`, `app/db/session.py`, SQLAlchemy 2.0 `JSONB` models (`MoaRegistry`, `Document`, `DocumentPage`, `DocumentClause`), and `tests/test_db_and_config.py`.
- [x] **CL 2.2 (`feat(moa-client)`):** Implement `app/services/moa_client.py` (on-call `PesticideData.aspx?$filter=...` + 4 sub-endpoints lookup with PostgreSQL snapshot upsert) + unit tests.
- [x] **Checkpoint / PR #2:** Review & merge `develop` → `main` for Component 2.

## Component 3: Pre-Processing, Constrained Decoding Schemas & Prompts

- [x] **CL 3.1 (`feat(schemas)`):** Implement `GroundedField[T]` Chain-of-Grounding (`verbatim_text` → `value` → `page` → `box_2d`) and 4 extraction schemas (`ProductLabelExtraction`, `MsdsExtraction`, `NotarizedCertExtraction`, `OfficialLetterExtraction`) + schema tests.
- [x] **CL 3.2 (`feat(parser)`):** Implement `preprocessor.py` (`PyMuPDF` + `Pillow` WebP normalizer), domain prompts, and `parser.py` (interleaved page-image batching + Gemini constrained decoding).
- [x] **Checkpoint / PR #3:** Review & merge `develop` → `main` for Component 3.

## Component 4: Deterministic Cascade Diff, Two-Phase Copilot & FastAPI Routes

- [x] **CL 4.1 (`feat(diff)`):** Implement `cascade_diff.py` across Old Label, New MSDS, MOA Registry, Change Certificate, and Official Letters + unit tests.
- [x] **CL 4.2 (`feat(copilot)`):** Implement the 4 agent tools (`tools.py`), `ComplianceAuditReport` schema, and Two-Phase Copilot (`copilot.py`) + unit tests.
- [x] **CL 4.3 (`feat(api)`):** Wire FastAPI endpoints (`routes_registry.py`, `routes_documents.py`, `routes_copilot.py`, `main.py`) + seed fixtures.
- [x] **Checkpoint / PR #4:** Review & merge `develop` → `main` for Component 4.

## Component 5: Angular + Angular Material UI & `<svg viewBox="0 0 1000 1000">` BBox Viewer

- [x] **CL 5.0 (`chore(frontend-scaffold)`):** Isolate pure Angular CLI & Angular Material scaffold (`npx @angular/cli@19 new frontend` + `ng add @angular/material` + `nginx.conf`) with zero custom business logic.
- [x] **CL 5.1 (`feat(ui-core)`):** Add `color-scheme: light dark` & `lang="zh-Hant"` root config, TypeScript models (`compliance.models.ts`), and `ComplianceApiService`.
- [x] **CL 5.2 (`feat(ui-viewer)`):** Build `BboxSvgViewerComponent` (inline `<svg role="img" viewBox="0 0 1000 1000" preserveAspectRatio="none">` over `fetchpriority="high"` `.webp` `<img>` with `width_px`/`height_px` zero-CLS sizing and `contain: layout style paint`), `CopilotAuditPanelComponent` (`@container` queries, semantic `<caption>`/`<th scope="col|row">` checklist, `>=24x24px` native `<button>` citations, bilingual `lang` tags, `<progress>` state), and native `<dialog>.showModal()` upload modal.
- [x] **Checkpoint / PR #5:** Review & merge `develop` → `main` for Component 5.

## Component 6: Polish, Multi-Volume Baselines, Entity Alias Mapping & UX Refinements

- [x] **CL 6.1 (`feat(backend-polish)`):** Chinese-first label formulation & LoA `issuing_agency` prompt fixes (`extraction_prompts.py`), 2400px/90q WebP sharpness (`config.py`), `volume_tag` multi-volume label baselines (`models.py`, `session.py`, `routes_documents.py`, `tools.py`), and global `EntityAlias` table + `/api/entity-aliases` routes + `cascade_diff.py` alias matching.
- [x] **CL 6.2 (`feat(ui-polish)`):** Remove MOA search button; replace top audit button with `載入基準文件` panel + bottom `執行合規審查` trigger; add 0.5x–4x zoom + hover magnifier loupe in `BboxSvgViewerComponent`; add `EntityAliasDialogComponent` bilingual confirmation; responsive layout polish across viewports.
- [x] **Checkpoint / PR #6:** Review & merge `develop` → `main` for Component 6.

## Component 7: Cloudflare Cloud-Native Deployment, R2, Alembic & CI/CD

- [x] **CL 7.1 (`feat(cloud-storage-db)`):** Add dual-mode `PageStorage` (local disk ↔ Cloudflare R2 S3 API), `postgres_ssl` support, Alembic migrations (`backend/alembic/`), `GET /api/copilot/audit/{reg_no}` read-only demo endpoint + seed fixture, and `ADR-001` Decision 5.
- [x] **CL 7.2 (`feat(cloudflare-ci)`):** Add `wrangler.jsonc`, `src/worker.ts` (Static Assets + R2 edge image serving + `ComplianceBackend` Container + `WRITE_ACCESS_TOKEN` / Zero Trust hybrid), and `.github/workflows/ci-cd.yml`.
- [x] **Checkpoint / PR #7:** Review & merge `develop` → `main` to trigger Cloudflare deploy.

## Component 8: Production Cloud Provisioning (GitHub, Cloudflare & Neon)

- [x] **CL 8.1 (`infra(neon)`):** Provision Neon Postgres `production` branch, run `alembic upgrade head` (`001_initial_schema`), and normalize `postgres://` + `postgresql://` URLs in `backend/app/config.py`.
- [x] **CL 8.2 (`infra(github-ci)`):** Configure GitHub Actions secrets (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `DATABASE_URL`), bump CI Node.js to 22 for Wrangler 4.166.0, and sanitize deploy token formatting in `.github/workflows/ci-cd.yml`.
- [x] **CL 8.3 (`infra(cloudflare)`):** Enable Workers Paid plan for Containers, register `*.workers.dev` subdomain, create R2 bucket `compliance-agent-pages` + S3 API token, create scoped Cloudflare deploy API token (`Workers Scripts: Edit`, `Workers R2 Storage: Edit`, `Containers: Edit`, `Account Settings: Read`, `User Details: Read`), configure Worker runtime Variables & Secrets, and attach Zero Trust Access (Self-hosted Workers + email OTP allowlist + `REQUIRE_CF_ACCESS=true`).

## Component 9: Framed-Sheet UI Polish & Typography

- [x] **CL 9.1 (`feat(ui)`):** Apply `design-taste-frontend` (`framed-sheet` layout, IBM Plex Sans/Mono + Noto Sans TC, SVG botanical compliance monogram), Light/Dark theme toggle (light default), opaque document library drawer, and `asciify-engine` `contour` animated backdrop with translucent workspace sheet, cards, and dialogs (`styles.scss`, `index.html`, `app.component.*`, `copilot-audit-panel.component.scss`, `bbox-svg-viewer.component.scss`, `package.json`).
- [x] **Checkpoint / PR #9:** Review & merge `develop` → `main` for Component 9 (merged via PR #13 `c6019cc`).

## Component 10: APHIA Regulatory Lookup, Crop Usage Diff & Audit UX (Post-MVP #1)

- [x] **CL 10.1 (`feat(aphia)`):** Add `aphia_client.py` + `002_aphia_records.py` migration for APHIA announcements, banned/carcinogen PDF parsing, 15-year assessments + pre-notices, evaluation dates, and `moa_registry.approved_usages_json` schema alignment (`session.py`, `routes_registry.py`, `routes_copilot.py`, `test_aphia_client.py`).
- [x] **CL 10.2 (`feat(crop-usage)`):** Extract label crop-usage tables (`extraction.py`, `extraction_prompts.py`), compare against MOA `approved_usages_json` (`tools.py`, `copilot.py`), and split findings into a dedicated `未登載使用範圍` `mat-tab` (`copilot-audit-panel.component.*`).
- [x] **CL 10.3 (`feat(ui-diff)`):** Replace per-citation buttons with a single `查看差異` side-by-side BBox viewer (`bbox-svg-viewer.component.*`, `app.component.*`) and widen the multi-entry upload dialog (`upload-dialog.component.scss`).
- [x] **CL 10.4 (`feat(observability,config)`):** Log Gemini model, latency, and token usage on success (`parser.py`, `copilot.py`, `main.py`) and set confirmed-active fallback chain `["gemini-3.6-flash", "gemini-3.5-flash-lite"]` (`config.py`).
- [x] **CL 10.5 (`fix(prompts,parser)`):** Skip TECO legalization cover pages, fix `issuing_authority` schema alignment, anchor LoA body/notary extraction, and bump `change_cert` to `MEDIA_RESOLUTION_HIGH` (`extraction_prompts.py`, `parser.py`).
- [ ] **Checkpoint / PR #10:** Push `develop` (`b113584`–`1c233c4`, `CL 10.5` + Component 10 docs) and merge `develop` → `main`.

## Component 11: Multi-Tenant Company Isolation & Cloudflare Access Identity (Post-MVP #2)

See detailed design in [`tasks/plan.md`](plan.md).

- [x] **Task 11.1 (`feat(edge-auth)`):** Verify `Cf-Access-Jwt-Assertion` (JWKS + `aud`) in `src/worker.ts`, strip client-spoofed `X-Auth-User-Email`, and forward verified email to `ComplianceBackend`. *(S: `src/worker.ts`, `src/accessAuth.ts`, `src/accessAuth.test.ts`)*
- [x] **Task 11.2 (`feat(db-tenancy)`):** Add `companies`, `company_users` (explicit allowlist), & `company_access_requests` and Alembic migration `003` adding `company_id` (backfilled to `default` / `public_demo`) + scoped unique constraints on `documents`, `entity_aliases`, `audit_reports`, and `chemical_evaluations`. *(M: `backend/app/db/models.py`, `backend/alembic/versions/003_*.py`, `backend/app/db/session.py`)*
- [x] **Checkpoint 1 (Tasks 11.1–11.2):** `node --test`, `tsc -p tsconfig.worker.json`, `alembic upgrade head`, and `pytest -q` pass.
- [x] **Task 11.3 (`feat(api-tenancy-docs)`):** Add FastAPI `get_current_company_user` dependency (explicit `company_users` allowlist), scope `/api/documents*` & R2 keys (`companies/{company_id}/pages/{doc_id}/...`) by `company_id`, and enforce tenant ownership on page-image fetches (allowing `public_demo` read-only). *(M: `backend/app/api/deps.py`, `routes_documents.py`, `storage.py`, `src/worker.ts`, tests)*
- [x] **Task 11.4 (`feat(api-tenancy-audit)`):** Scope `routes_copilot.py`, `routes_entity_aliases.py`, `routes_registry.py` (`chemical_evaluations`), `tools.py`, and audit `cache_key` by `company_id` (keeping `moa_registry` & `aphia_*` global, and `public_demo` `農藥進99001` open for read-only demo). *(M: `routes_copilot.py`, `routes_entity_aliases.py`, `routes_registry.py`, `tools.py`, tests)*
- [x] **Checkpoint 2 (Tasks 11.3–11.4):** Cross-company isolation tests prove Company A users share data while Company B cannot read/modify/delete Company A's documents, aliases, or audits.
- [x] **Task 11.5 (`feat(ui-identity)`):** Add `GET /api/me` and `POST /api/access-requests`, show active user email + company badge in Angular top bar, add **"Try Demo Audit"** (`農藥進99001`) button, and add **"Join Company"** application dialog for un-allowlisted users. *(M: `routes_identity.py`, `compliance-api.service.ts`, `app.component.*`, `join-company-dialog.component.ts`)*
- [ ] **Task 11.6 (`feat(admin-onboarding)`):** Add `POST /api/access-requests` plus admin-only (`role == 'admin'`) `GET /api/admin/access-requests` and `POST /api/admin/company-users` to assign/approve a user into a company. *(S: backend routes + tests)*
- [ ] **Task 11.7 (`feat(rate-limit)`):** Add Cloudflare Workers `ratelimits` binding in `wrangler.jsonc` keyed by verified email on Gemini `POST` routes in `src/worker.ts`. *(S: `wrangler.jsonc`, `src/worker.ts`)*
- [ ] **Checkpoint / PR #11:** Review & merge `develop` → `main` for Component 11.

## Future Enhancements (Post-MVP)

- [x] **APHIA Announcements & Chemical Assessments:** Add separate lookup/sync for `pesticide.aphia.gov.tw` official announcements (`農藥公告`), banned/restricted/carcinogen lists, and ACRI chemical risk assessments (not exposed in `EIR027`).
- [ ] **Email / Gmail Webhook Ingestion:** Accept emails with attachments from allowlisted users (e.g. Zero Trust / owner allowlist via Gmail Pub/Sub webhook or Cloudflare Email Routing), auto-classify document type (`label` / `loa` / `msds`), parse, and store into R2 + Neon Postgres.
- [ ] **Multi-Tenant Storage Isolation & Per-User Rate Limiting:** Partition R2 object prefixes (`orgs/{org_id}/...`) and Postgres rows by user/organization using verified Zero Trust identity (`Cf-Access-Authenticated-User-Email` / JWT), and enforce per-user Gemini `POST` rate limits in `src/worker.ts` via Cloudflare Workers `ratelimits` binding (or Durable Object/DB quota).
- [ ] **Baseline File Archive & Download by `reg_no` with TTL Cleanup:** Persist original uploaded files in R2, expose download endpoints for baseline files per `reg_no`, and automatically expire non-baseline / non-latest files via R2 Lifecycle Rules or scheduled cleanup.
