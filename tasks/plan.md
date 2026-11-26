# Component 11 Plan: Company-Scoped Multi-Tenancy & Cloudflare Access Identity

## 1. Identity Source (Cloudflare Zero Trust Access)
- When `REQUIRE_CF_ACCESS=true`, Cloudflare Access authenticates the user and attaches `Cf-Access-Jwt-Assertion` (RS256 JWT containing `email`, `sub`, `aud`, `exp`) and `Cf-Access-Authenticated-User-Email` to every request reaching `src/worker.ts`.
- **Trust boundary:** `src/worker.ts` verifies `Cf-Access-Jwt-Assertion` via WebCrypto against `https://<CF_ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs` (cached JWKS) + `aud` (`CF_ACCESS_AUD`), strips any client-supplied `X-Auth-User-Email` header, and injects the verified `X-Auth-User-Email` before forwarding to `ComplianceBackend`.
- **Company membership:** Resolved exclusively via explicit `company_users` allowlist rows (`email -> company_id`, `role`). Never auto-map email domains. Users not in `company_users` receive `403 Forbidden` on company-scoped endpoints when Access auth is active; local dev without `REQUIRE_CF_ACCESS=true` falls back to a configurable local default user/company (`default`).

## 2. Database Split Strategy (Shared Global Regulatory Data vs. Company-Scoped Workspace Data)
- **Shared global tables (public official data, shared across all companies):**
  - `moa_registry`
  - `aphia_records`
  - `aphia_query_cache`
- **New identity & tenancy tables:**
  - `companies` (`id`, `slug`, `name`, `created_at`)
  - `company_users` (`email` PK, `company_id` FK -> `companies.id`, `role` `'member'|'admin'`, `created_at`)
  - `company_access_requests` (`id` PK, `email`, `requested_company_name`, `note`, `status` `'pending'|'approved'|'rejected'`, `reviewed_by`, `created_at`, `reviewed_at`)
- **Company-scoped tables (`company_id` FK -> `companies.id`):**
  - `documents` (cascades to `document_pages` and `document_clauses`): update unique active-baseline index `uq_documents_active_baseline` to `(company_id, reg_no, doc_type, volume_tag)`.
  - `entity_aliases`: update unique constraint `uq_entity_alias_pair` to `(company_id, zh_name, en_name)`.
  - `audit_reports`: add `company_id` and include `company_id` in `cache_key`.
  - `chemical_evaluations`: change PK from `(common_name)` to `(company_id, common_name)`.
- **R2 Object Storage:**
  - Namespace page keys as `companies/{company_id}/pages/{doc_id}/page_{n}.webp` in `PageStorage`.
  - Protect `GET /api/documents/{doc_id}/pages/{page}/image` so Company B cannot fetch Company A's R2 objects by guessing `doc_id`.

## 3. Ordered Vertical Slices & Checkpoints
1. **Task 11.1 (`feat(edge-auth)`):** Verify `Cf-Access-Jwt-Assertion` (JWKS + `aud`) in `src/worker.ts` / `src/accessAuth.ts`, strip spoofed `X-Auth-User-Email`, inject verified email, and add Node unit tests (`src/accessAuth.test.ts`).
2. **Task 11.2 (`feat(db-tenancy)`):** Add `companies` & `company_users` ORM models and Alembic migration `003` adding `company_id` (backfilled to `default`) + scoped unique constraints on `documents`, `entity_aliases`, `audit_reports`, and `chemical_evaluations`.
3. **Checkpoint 1 (After Tasks 11.1–11.2):** `node --test`, `tsc -p tsconfig.worker.json`, `alembic upgrade head`, and `pytest -q` pass.
4. **Task 11.3 (`feat(api-tenancy-docs)`):** Add FastAPI `get_current_company_user` dependency (strict explicit `company_users` lookup), scope `/api/documents*` & R2 keys (`companies/{company_id}/pages/{doc_id}/...`) by `company_id`, and enforce tenant ownership on page-image fetches.
5. **Task 11.4 (`feat(api-tenancy-audit)`):** Scope `routes_copilot.py`, `routes_entity_aliases.py`, `routes_registry.py` (`chemical_evaluations`), `tools.py`, and audit `cache_key` by `company_id` (keeping `moa_registry` & `aphia_*` global, and preserving public `GET /api/copilot/audit/{reg_no}` for demo `reg_no` if configured).
6. **Checkpoint 2 (After Tasks 11.3–11.4):** Cross-company isolation tests prove users in Company A share data while Company B cannot read/modify/delete Company A's documents, aliases, or audits.
7. **Task 11.5 (`feat(ui-identity)`):** Add `GET /api/me` (returning `{ email, company_id, company_name, role, pending_request }`), show active user + company badge in Angular top bar, and add a **"Try Demo Audit" (`農藥進99001`)** button + **"申請加入公司 (Join Company)"** dialog for un-allowlisted users.
8. **Task 11.6 (`feat(admin-onboarding)`):** Add `POST /api/access-requests` (authenticated user files a company join application), plus admin-only (`role == 'admin'`) `GET /api/admin/access-requests` and `POST /api/admin/company-users` to assign/approve a user into a `company_id`.
9. **Task 11.7 (`feat(rate-limit)`):** Add Cloudflare Workers `ratelimits` binding in `wrangler.jsonc` keyed by verified email on Gemini `POST` routes in `src/worker.ts`.

## 4. Resolved Decisions
- Explicit `company_users` allowlist rows (no email-domain auto-mapping); un-allowlisted users can submit a Join Company application (`company_access_requests`) and an admin assigns their `company_id` via `POST /api/admin/company-users`.
- Public read-only demo (`GET /api/copilot/audit/農藥進99001`, `GET /api/registry/農藥進99001`, and neutralized `public_demo` page images) remains open without login.
