# Security & Hardening Ledger

Tracks implemented security controls, trust boundaries, and operational requirements across `compliance_agent`.

---

## 1. Edge Authentication & Header Trust Boundary (`src/accessAuth.ts`, `src/worker.ts`)

- **Cloudflare Access RS256 JWT Verification (`verifyCloudflareAccessJwt`):**
  - Verifies `Cf-Access-Jwt-Assertion` via WebCrypto (`RSASSA-PKCS1-v1_5` / `SHA-256`) against `https://<CF_ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs`.
  - Validates `alg === "RS256"`, `kid`, `exp`, `nbf`, mandatory `aud` (`CF_ACCESS_AUD`), `iss` (`https://<CF_ACCESS_TEAM_DOMAIN>`), and non-empty `email`.
  - **DoS mitigation:** Caches JWKS for 5 minutes with a 30-second minimum refresh cooldown (`JWKS_MIN_REFRESH_INTERVAL_MS`) so unknown `kid` floods cannot amplify outbound fetches.
- **Anti-Spoofing Header Sanitization (`prepareContainerRequest`):**
  - Always strips any client-supplied `X-Auth-User-Email` header before forwarding `/api/*` requests to `ComplianceBackend`, injecting `X-Auth-User-Email` **only** when JWT verification succeeds.
- **Fail-Closed Write Gate (`isAuthorizedWrite`):**
  - When `WRITE_ACCESS_TOKEN` is set, compares `X-Write-Token` / `Authorization: Bearer` in constant time (`SHA-256` + XOR).
  - When `REQUIRE_CF_ACCESS=true`, writes strictly require `verifiedIdentity !== null` (no fallback to raw header presence).
- **Security Headers (`withSecurityHeaders`):**
  - Sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: strict-origin-when-cross-origin` on all Worker responses.
- **Production Requirement:** In Cloudflare Dashboard → `compliance-agent` → **Variables and Secrets**, set `REQUIRE_CF_ACCESS=true`, `CF_ACCESS_TEAM_DOMAIN`, and `CF_ACCESS_AUD`.

---

## 2. Database Tenancy & Integrity (`backend/app/db/models.py`, `backend/alembic/versions/003_company_tenancy.py`)

- **Explicit Company Allowlist (`company_users`):**
  - Maps verified lowercase `email` → `company_id` (`member` | `admin`). Never auto-maps email domains (preventing public domains like `gmail.com` from sharing data).
- **Path-Safe Tenant IDs (`ck_company_id_safe_path`):**
  - Rejects `/`, `\`, and `..` in `companies.company_id` so `companies/{company_id}/pages/...` storage prefixes cannot traverse paths.
- **Lowercase Email Enforcement (`ck_company_user_email_lower`, `ck_access_request_email_lower`):**
  - Enforces `email = lower(email)` in PostgreSQL to match normalized JWT emails and prevent case-mismatch authorization bypasses or `403`s.
- **Access Request Flood Prevention (`uq_access_requests_pending_email`):**
  - Partial unique index on `company_access_requests(email) WHERE status = 'pending'` limits each user to one active pending join request.
- **Scoped Uniqueness:**
  - `uq_documents_active_baseline` scoped to `(company_id, reg_no, doc_type, volume_tag)`.
  - `uq_entity_alias_pair` scoped to `(company_id, zh_name, en_name)`.
  - `chemical_evaluations` PK scoped to `(company_id, common_name)`.

---

## 3. Supply Chain & Secrets Hygiene

- **Secret Storage:** Runtime secrets (`DATABASE_URL`, `GEMINI_API_KEY`, R2 credentials) live in Cloudflare Worker **Variables and Secrets**; CI/CD uses only `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `DATABASE_URL` in GitHub Secrets.
- **Root npm Audit:** `npm audit --omit=dev` verified `0 vulnerabilities` (Task 11.1).

---

## 4. Planned Controls (Tasks 11.3–11.7)

- [x] **Task 11.3:** Enforced `company_id` ownership on `/api/documents*` (`deps.py`, `routes_documents.py`), namespaced R2 keys under `companies/{company_id}/pages/{doc_id}/...` with `validate_company_id` (`storage.py`), removed the unauthenticated R2 page-image shortcut in `src/worker.ts`, forwarded `REQUIRE_CF_ACCESS` to `ComplianceBackend`, and set `Cache-Control: private` for company page images (`public` only for `public_demo`).
- [ ] **Task 11.4:** Scope `/api/copilot*`, `/api/entity-aliases*`, and `chemical_evaluations` by `company_id`.
- [ ] **Task 11.6:** Enforce `role == 'admin'` on `GET /api/admin/access-requests` and `POST /api/admin/company-users`.
- [ ] **Task 11.7:** Add Cloudflare Workers `ratelimits` binding keyed by verified email on Gemini `POST` routes.
