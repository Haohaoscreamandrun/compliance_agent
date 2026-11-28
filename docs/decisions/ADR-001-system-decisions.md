<!-- cSpell:words JSONB pgvector PyMuPDF Docling Qdrant asyncpg MOAOPD PesticideGropty PesticideBrcode PesticideLicImage APHIA MinerU Reducto Surya agrochemical MSDSs Pydantic WebP webp ymin xmin ymax xmax httpx Streamlit Gradio VertexAI fitz viewBox fetchpriority WCAG -->
# ADR-001: Core System Architecture & Tooling Decisions

## Status
Accepted

## Date
2026-11-15 (Amended 2026-11-17 with Modern Web Guidance audit findings)

## Context
Regulatory Affairs (RA) teams managing Taiwan agrochemical and specialty chemical registrations (~10–500 products) must cross-reference English/bilingual global documents (notarized factory/source-change certificates, 16-section GHS MSDSs) against Traditional Chinese local records (Taiwan MOA `EIR027` registry data, product label PDFs exported from graphic design software, and official government letters 公文).

Every compliance finding must carry exact spatial provenance (`[doc_id, page, box_2d]`) so an RA specialist can click any recommended label update and visually verify the source clause or label field. Additionally, the repository is public while local runs handle confidential certificates and Paid-tier API credentials.

## Decision

1. **Database Layer — Single PostgreSQL 16 (`JSONB`):**
   * Store cached MOA registry snapshots, extracted document schemas (`GroundedField[T]`), normalized page dimensions (`width_px`, `height_px`), and flattened document clauses (`box_2d`) in a single PostgreSQL 16 database using `JSONB` and canonical join keys (`reg_no`, `common_name`, `pesticide_code`, `company_name`, `doc_type`).
   * Construct database connections via `SQLAlchemy URL.create()` from discrete `POSTGRES_*` variables in `.env` so special characters in passwords and host-vs-Docker overrides (`POSTGRES_HOST=db`) never conflict.
2. **Government Registry Ingestion — On-Call `httpx` Lookup Against Official MOA `EIR027` OpenData API:**
   * Rather than bulk-downloading thousands of permits on a weekly cron schedule, retrieve the target product's MOA registry data **on-call** (`GET /api/registry/{reg_no}` or inside `get_product_baseline`) directly from `https://data.moa.gov.tw/Service/OpenData/FromM/PesticideData.aspx?$filter=...` using UTF-8 URL-encoded `$filter` (e.g., by `許可證號` or `中文名稱`).
   * For the matched product (`reg_no = 許可證字 + 許可證號`), concurrently fetch its linked JSON sub-endpoints (`PesticideDetail.aspx`, `PesticideLicImage.aspx`, `PesticideBrcode.aspx`, `PesticideGropty.aspx`) and upsert the snapshot into `moa_registry` (`ON CONFLICT (reg_no) DO UPDATE`, falling back to the cached PostgreSQL row if the external MOA API is temporarily unreachable).
3. **Document Pre-Processing & Parsing — In-RAM `PyMuPDF` / `Pillow` + `Gemini 2.5 Flash / Flash-Lite` Constrained Decoding:**
   * Product labels are uploaded as JPEG/image files (including APHIA-annotated label images with drawn/handwritten modification instructions), while MSDSs, LoA/COR certificates, and 公文 are PDFs. Read all uploaded PDFs strictly in RAM (`fitz.open(stream=pdf_bytes, filetype="pdf")`) and raster images via `Pillow` (`ImageOps.exif_transpose`), discarding raw bytes immediately after rendering/normalizing pages to `<= 1600px` `.webp` images (`quality=85`) under `{STORAGE_DIR}/pages/{doc_id}/`.
   * Serve page images through `/api/documents/{doc_id}/pages/{page}/image` (with `Cache-Control: private, max-age=3600, immutable`) so Nginx reverse-proxying (`/api/*` → `http://backend:8000` with `client_max_body_size 25m` and `proxy_read_timeout 120s`) works identically in Docker Compose and local CLI dev.
   * Feed interleaved page batches (`["=== PAGE {global_page_no} ===", img, ...]`, up to 8 pages per call or 5-page windows for longer MSDSs) to `Gemini 2.5 Flash / Flash-Lite` with `response_mime_type="application/json"` and `response_schema`.
   * For multi-window extraction, top-level section fields are nullable (`GroundedField[T] | None = None`) and list fields default to `[]` so constrained decoding never forces hallucination of sections outside the current window; window results merge deterministically by accepting the first non-null field whose `page` falls within the window's `[start_page, end_page]` bounds and concatenating/deduplicating list items.
   * Enforce **Chain-of-Grounding (`GroundedField[T]`)** with fields declared in strict autoregressive order (`verbatim_text` → `value` → `page` → `box_2d`) plus Pydantic post-validators enforcing 4-tuple `[ymin, xmin, ymax, xmax]` bounds (`0 <= coord <= 1000`, `ymin < ymax`, `xmin < xmax`) and `1 <= page <= page_count`.
4. **Agentic Compliance Copilot — Two-Phase Execution (`Gemini 2.5 Pro / Flash`):**
   * **Phase 1 (Tool Loop):** Execute 4 deterministic/SQL function-calling tools (`inspect_uploaded_doc`, `get_product_baseline`, `search_regulatory_history`, `run_cascade_diff`) without `response_schema`. Baseline queries (`get_product_baseline` and `run_cascade_diff`) explicitly exclude the candidate `uploaded_doc_id` (`is_active_baseline = TRUE AND doc_id != uploaded_doc_id`) to prevent self-diffing when auditing a newly uploaded MSDS or label.
   * **Phase 2 (Constrained Synthesis):** Detach tools and enforce `response_schema=ComplianceAuditReport` via constrained decoding, validating that every cited `(doc_id, page)` exists in the Phase 1 tool context before returning the strongly-typed JSON (`section_1_document_evidence` and `section_2_label_update_checklist`) to the frontend.
5. **Frontend Stack — Angular 19 + Angular Material + Inline `<svg viewBox="0 0 1000 1000">` Overlay (`modern-web-guidance` compliant):**
   * **Vector SVG BBox Overlay over `<canvas>`:** Layer an inline `<svg role="img" viewBox="0 0 1000 1000" preserveAspectRatio="none">` directly over the `.webp` page `<img fetchpriority="high" [attr.width]="page.width_px" [attr.height]="page.height_px">` inside an aspect-ratio container (`CLS = 0`). Because Gemini's `box_2d` is `[ymin, xmin, ymax, xmax]` on a `0–1000` grid, `<rect [attr.x]="xmin" [attr.y]="ymin" [attr.width]="xmax - xmin" [attr.height]="ymax - ymin">` maps 1:1 with zero JavaScript pixel math or `ResizeObserver` redraws, stays sharp on HiDPI displays, and avoids volatile single-engine `canvas-html` features.
   * **Modern Web & WCAG 2.1 AA Standards:** Use CSS `@container` queries (`container-type: inline-size`) on the split panes, `contain: layout style paint` on the viewer pane, `content-visibility: auto` with `contain-intrinsic-block-size` on checklist items, bilingual `lang="zh-Hant"` / `lang="en"` attributes on mixed-language quotes, semantic `<caption>` + `<th scope="col|row">` table markup, native `<button type="button">` (`>= 24x24px`) citation triggers, `<progress>` during Copilot execution, native `<dialog>.showModal()` for document uploads, and `:root { color-scheme: light dark; }` theming.

## Alternatives Considered & Why Forfeited

### 1. Bulk Weekly Cron Sync (`APScheduler`) vs. On-Call MOA `EIR027` Retrieval
* **Pros of Bulk Cron:** Pre-populates every permit in Taiwan ahead of time.
* **Cons of Bulk Cron:** Requires a background scheduler (`APScheduler`), batch deduplication, `payload_hash` partial-failure retry state, and thousands of unnecessary sub-endpoint HTTP calls for permits that are not being audited.
* **Rejected:** Querying `PesticideData.aspx?$filter=...` on-call for the single `reg_no` being audited takes `< 1 second`, guarantees the freshest live MOA data at the exact moment of the audit, and caches the result in `moa_registry` with zero background scheduler complexity.

### 2. Dual-Store PostgreSQL + Qdrant Cloud & Vector-First RAG
* **Pros:** Dedicated vector index for large-scale semantic search across millions of chunks.
* **Cons:** Requires synchronizing document inserts, re-uploads, and deletes across two independent databases without distributed transactions; top-$k$ vector retrieval on a 2–15 page certificate risks omitting a mandatory compliance clause that ranks below $k$; vector cosine similarity across English (`"Carbendazim"`) and Traditional Chinese (`"貝芬替"`) is unreliable compared to canonical join keys (`reg_no`, `common_name`, `pesticide_code`).
* **Rejected:** At 10–500 products, exact SQL filtering (`WHERE doc_id = $1` or `WHERE reg_no = $1`) guarantees `100%` clause recall with zero dual-store sync bugs. If fuzzy concept search across a large archive of unindexed historical 公文 is needed later, PostgreSQL's built-in `pgvector` extension handles it inside the same database table.

### 3. Multi-Engine OCR Stack (`IBM Docling`, `Mistral OCR`, `DocLayout-YOLO`, `RapidOCR`, `MinerU`, `Marker`)
* **Pros:** Local layout detection (`Docling`/`YOLO`) can extract text blocks without VLM token cost on clean English PDFs.
* **Cons:** Different coordinate origins and scaling across engines (`PyMuPDF` bottom-left points vs. `YOLO` pixel boxes vs. `Mistral OCR` blocks) cause visual overlay drift; traditional OCR and layout models fail on Taiwan official letters (公文 red stamps overlapping text, vertical/horizontal layouts, ROC calendar dates `中華民國117年`) and cannot classify visual GHS hazard diamonds (`GHS01–GHS09`) with semantic field binding in a single pass; bundles multi-GB PyTorch/CUDA weights into containers.
* **Rejected:** Unified `PyMuPDF` + `Pillow` + `Gemini 2.5 Flash / Flash-Lite` constrained decoding extracts bilingual text, ROC dates, and visual `GHS01–GHS09` icons with native `[0–1000]` `box_2d` coordinates in one pass for pennies.

### 4. Headless Browser Scraping (`Crawl4AI` / `Playwright`) for Government Registry
* **Pros:** Can scrape portals that lack structured APIs.
* **Cons:** Heavy Chromium container footprint, brittle DOM selectors, and anti-bot/CAPTCHA failures.
* **Rejected:** Taiwan MOA publishes the official `MOAOPD-API-EIR027` JSON API (`PesticideData.aspx`) supporting `$top`, `$skip`, `$filter`, and 4 JSON sub-endpoints (`PesticideDetail`, `PesticideLicImage`, `PesticideBrcode`, `PesticideGropty`), which a plain `httpx` client fetches on-call in `< 1 second`.

### 5. Single-Pass `tools=[...]` + `response_schema` or Unconstrained Markdown Copilot Replies
* **Pros:** One fewer API call at the end of the Copilot loop.
* **Cons:** Combining active function calling (`tools`) and strict `response_schema` constrained decoding in the same Gemini generation turn causes API mode conflicts; conversely, returning unconstrained Markdown forces the frontend to regex-parse tables and citation coordinates.
* **Rejected:** The Two-Phase Copilot split (Phase 1 tool loop → Phase 2 constrained `ComplianceAuditReport` JSON synthesis) guarantees conflict-free tool execution and `100%` schema-valid JSON for Angular Material.

### 6. Raw HTML5 `<canvas>` Pixel Math or Streamlit / Gradio UI
* **Pros:** Raw `<canvas>` can draw arbitrary 2D bitmaps; Streamlit/Gradio allow rapid Python prototyping.
* **Cons:** Raw `<canvas>` ignores `devicePixelRatio` by default (blurring on HiDPI displays), requires `ResizeObserver` coordinate recalculation when panes resize, hides bounding boxes from screen readers and keyboard focus (`expose-canvas-content-to-browser-features` flags `canvas-html` as a volatile single-engine feature), and Streamlit re-runs the script on every click.
* **Rejected:** Angular 19 + Angular Material with Signals and an inline `<svg role="img" viewBox="0 0 1000 1000" preserveAspectRatio="none">` overlay maps `0–1000` `box_2d` coordinates 1:1 in hardware-accelerated vector space with native accessibility and zero JS coordinate math.

## Consequences
* **Positive:** Zero dual-database synchronization bugs; zero background cron schedulers (`APScheduler` eliminated); zero headless browser or local GPU dependencies; `100%` recall on uploaded certificates via SQL `doc_id` filtering; raw confidential PDFs are never persisted to disk; every extracted field and Copilot checklist item is guaranteed by token-level constrained decoding + Pydantic bounds validators to carry valid `[doc_id, page, box_2d]` rendered crisply via `<svg viewBox="0 0 1000 1000">`.
* **Trade-offs & Mitigations:**
  * *Paid-Tier API Requirement:* `GEMINI_API_KEY` must belong to a billing-enabled (Paid tier) Google AI Studio project (or `GOOGLE_GENAI_USE_VERTEXAI=true`) so confidential documents are excluded from model training.
  * *Page-Image Lifecycle:* While raw PDFs are processed purely in RAM, rendered `.webp` page images are stored in `{STORAGE_DIR}/pages/{doc_id}/` and served via `/api/documents/{doc_id}/pages/{page}/image` so the Angular `<svg>` overlay viewer can display the document background behind `box_2d` highlights; deleting a document via the API/UI purges its `{STORAGE_DIR}/pages/{doc_id}/` directory alongside its PostgreSQL rows.

---

## Decision 5 — Cloudflare Cloud-Native Deployment, Dual-Mode R2 Storage, Alembic & Hybrid Edge Auth (Component 7)

* **Context:** Local `docker-compose.yml` suffices for development, but production cloud hosting on Cloudflare requires stateless container execution (ephemeral disk), managed TLS PostgreSQL, automated schema migrations, and protection of Gemini API quota.
* **Chosen Architecture:**
  * **Frontend & Edge Gateway (`wrangler.jsonc`, `src/worker.ts`):** Angular 19 `dist/frontend/browser` is served via **Cloudflare Workers Static Assets** (`not_found_handling: "single-page-application"`, `run_worker_first: ["/api/*"]`). `worker.ts` serves `GET /api/documents/:docId/pages/:page/image` directly from the **Cloudflare R2** `PAGES_BUCKET` binding (`Cache-Control: public, max-age=86400, immutable`) and proxies all other `/api/*` traffic to the FastAPI **Cloudflare Container** (`ComplianceBackend` Durable Object, `sleepAfter = "10m"`, `max_instances = 3`).
  * **Dual-Mode `PageStorage` (`backend/app/services/storage.py`):** Uses local `{STORAGE_DIR}/pages/` when `R2_BUCKET` is unset (preserving zero-config `docker compose` and offline `pytest`) and Cloudflare R2 via S3 API (`boto3`) when `R2_BUCKET` + `R2_ENDPOINT_URL` are configured.
  * **Managed PostgreSQL & Alembic (`backend/alembic/`):** Connects to **Neon Postgres** via `POSTGRES_SSL=true` (`asyncpg` `ssl="require"`) with versioned Alembic migrations run in GitHub Actions (`alembic upgrade head`) prior to `wrangler deploy`.
  * **Hybrid Read-Only Demo + Write Gate:** `GET` endpoints (including `GET /api/copilot/audit/{reg_no}` returning PostgreSQL-cached `audit_reports` and `seed.py` demo fixtures) remain open for public read-only inspection, while `POST`/`PATCH /api/*` writes are gated by **Cloudflare Zero Trust Access** on a custom domain (with an optional `WRITE_ACCESS_TOKEN` header check in `worker.ts` for `*.workers.dev` deployments).
* **Forfeited Alternatives:**
  * *Cloudflare Python Workers (Pyodide):* Cannot run native C/C++ extensions (`pymupdf`) or local filesystem rendering.
  * *Cloudflare Tunnel (`cloudflared`) to local PC:* Requires an always-on host machine and does not exercise cloud-native CI/CD, R2 object storage, or versioned database migrations.

---

## Decision 6 — Production Provisioning across GitHub, Cloudflare & Neon (Component 8)

* **Context:** Taking Component 7 from code to live production on `*.workers.dev` surfaced platform-specific requirements across Neon Postgres, GitHub Actions, and Cloudflare Workers/Containers/Zero Trust.
* **Chosen Configuration:**
  * **Neon Postgres (`production` branch):** Provisioned managed PostgreSQL with Alembic `001_initial_schema (head)` applied in CI prior to Worker deploy; `Settings.sqlalchemy_database_url` normalizes both `postgresql://` and `postgres://` to `postgresql+asyncpg://` and strips `sslmode`/`channel_binding` so `asyncpg` uses `ssl="require"` cleanly. *(Chosen over Cloudflare Hyperdrive because Hyperdrive is a connection pooler for JS Workers, whereas the Python FastAPI + `asyncpg` container manages its own SQLAlchemy pool directly over TLS.)*
  * **GitHub Actions CI/CD (`.github/workflows/ci-cd.yml`):** Uses Node.js 22 (minimum required by `wrangler@4.166.0`), stores only CI/deploy secrets (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `DATABASE_URL`) in GitHub Secrets, and sanitizes accidental whitespace/quotes/`Bearer ` prefixes before `npx wrangler deploy`.
  * **Cloudflare Workers Paid, R2 & `*.workers.dev`:** Upgraded account to **Workers Paid** (required by Cloudflare Containers `/containers/me`), registered a `*.workers.dev` subdomain (avoiding the need for a custom domain for either same-origin `/api/*` calls or outbound Gemini HTTPS calls), created R2 bucket `compliance-agent-pages` with scoped S3 API credentials, and scoped the CI API token to `Workers Scripts: Edit`, `Workers R2 Storage: Edit`, `Containers: Edit`, `Account Settings: Read`, and `User Details: Read`.
  * **Runtime Secrets & Zero Trust Access:** Runtime secrets (`DATABASE_URL`, `GEMINI_API_KEY`, `R2_ENDPOINT_URL`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `REQUIRE_CF_ACCESS=true`) live in Cloudflare Worker **Variables and Secrets** (never in GitHub Secrets or git). Write protection uses **Cloudflare Zero Trust → Self-hosted → Workers** (Cloudflare One-Time PIN email allowlist), which works directly on `*.workers.dev` without needing a custom domain.
* **Forfeited / Deferred Alternatives:**
  * *Cloudflare Dashboard WAF Rate Limiting Rules:* Require a custom domain Zone proxied through Cloudflare and cannot target `*.workers.dev`; per-user Gemini `POST` rate limiting is deferred to a Workers `ratelimits` binding in `src/worker.ts` + `wrangler.jsonc` (Post-MVP).
  * *Static `WRITE_ACCESS_TOKEN` for UI users:* Angular does not prompt for an API token header, so Zero Trust Access (`Cf-Access-Jwt-Assertion`) is preferred for interactive browser users while `WRITE_ACCESS_TOKEN` remains available for headless API clients.

---

## Decision 7 — APHIA Announcements, Crop-Usage Verification, Side-by-Side Diff & Gemini Fallback (Component 10)

* **Context:** MOA `EIR027` (`PesticideData.aspx`) does not expose APHIA official announcements (`農藥公告`), banned/carcinogen lists, or 15-year ACRI chemical risk assessments, and multi-crop labels require verifying registered crop/pest/dosage rows against MOA `approved_usages_json` without cluttering the main compliance checklist.
* **Chosen Architecture:**
  * **APHIA Multi-Source Client & Migration (`backend/app/services/aphia_client.py`, `backend/alembic/versions/002_aphia_records.py`):** Queries APHIA announcements, 15-year chemical assessments + pre-notices, and parses the official carcinogen PDF with evaluation dates persisted in PostgreSQL (`moa_registry.approved_usages_json` aligned in both `002_aphia_records.py` and `session.py` startup).
  * **Label Crop-Usage Diff & Tab Split (`extraction.py`, `tools.py`, `copilot.py`, `copilot-audit-panel.component.html`):** Extracts structured crop-usage rows from product labels, compares them deterministically against MOA `approved_usages_json`, and surfaces unregistered crop usages in a dedicated `未登載使用範圍` `mat-tab` separate from the main field checklist.
  * **Side-by-Side `查看差異` Viewer (`bbox-svg-viewer.component.*`, `copilot-audit-panel.component.html`):** Replaces per-citation coordinate buttons with a single `查看差異` action per finding that opens side-by-side document comparison with synchronized `<svg viewBox="0 0 1000 1000">` bounding-box highlights.
  * **Gemini Observability & Fallback Chain (`backend/app/config.py`, `parser.py`, `copilot.py`):** Logs active model name, latency, and token usage on successful Gemini calls, and configures `gemini_parse_fallback_models` to `["gemini-3.6-flash", "gemini-3.5-flash-lite"]` behind `gemini-3.8-flash`.
* **Forfeited Alternatives:**
  * *Inline Crop-Usage Rows in the Main Checklist Table:* Mixing dozens of crop/pest rows into the primary label-field table obscures core registration mismatches; isolating them into `未登載使用範圍` keeps the primary audit scannable.
  * *Per-Citation Button Clusters:* Multiple inline `p.N` buttons per row created visual clutter and forced manual tab-switching between source documents; a single `查看差異` side-by-side view surfaces both compared documents at once.

---

## Decision 8 — Company Credit Pool, 5 Free AI Calls, Multi-Service Ledger & NewebPay Checkout (Component 12)

* **Context:** With `company_id` isolation in place (Component 11), Gemini AI costs must be metered per company and attributed to individual `user_email` callers, with a 5-call free onboarding grant and TWD pay-as-you-go top-ups.
* **Chosen Architecture:**
  * **Shared Company Wallet & Extensible Ledger (`company_billing_accounts`, `usage_ledger`, `004_billing_usage.py`):** Each company holds one shared pool (`free_calls_remaining = 5`, `credit_balance_micros` in USD micros `1e-6`, `billing_exempt`), while `usage_ledger` records every call with `service_name`, `unit`, `quantity`, `metadata_json`, `cost_micros`, and `user_email` so future paid services can share the same wallet.
  * **Successful-AI-Only Metering (`billing.py`, `parser.py`, `copilot.py`):** Only non-cached `POST /api/documents/upload` and `POST /api/copilot/audit/{reg_no}` calls that actually invoke Gemini and succeed decrement `free_calls_remaining` or deduct `token_cost × 1.5×`. Admins (`role == 'admin'`) and `billing_exempt` companies bypass `402` paywalls while still logging `charge_source='admin_exempt'`.
  * **NewebPay (藍新金流) MPG Hosted Checkout (`routes_billing.py`, `src/worker.ts`):** Fixed TWD packs (`NT$150`, `NT$500`, `NT$1,500`) redirect via signed `TradeInfo` (AES-256-CBC) + `TradeSha` (SHA-256) form POST; `POST /api/billing/newebpay/notify` and `/return` are exempted from Cloudflare Access write auth in `src/worker.ts` and verified via `TradeSha` + `MerchantOrderNo` idempotency. An admin manual credit endpoint (`POST /api/admin/companies/{company_id}/credits`) enables immediate top-ups prior to merchant KYC approval.
  * **Path-Scoped Cloudflare Access Bypass on `*.workers.dev` (`Compliance Agent NewebPay Callbacks`):** NewebPay's server-to-server `POST /api/billing/newebpay/notify` carries no Cloudflare Access cookie and would otherwise be redirected (`302`) to Access login. Because the Zero Trust **Workers** destination type exposes no **Path** field (whole-Worker scope only), the bypass application was configured under **Self-hosted → Public hostnames** targeting `compliance-agent.j842671395.workers.dev/api/billing/newebpay/notify` and `/api/billing/newebpay/return` (`Action: Bypass`, `Include: Everyone`), leaving the main `compliance-agent` Access app (`Allow Owner`) untouched. Verified live (`POST /api/billing/checkout` still `302`s to Access login while `POST /api/billing/newebpay/notify` reaches the Worker directly).
  * **Sandbox-First Runtime Provisioning (`PUBLIC_BASE_URL`, `NEWEBPAY_*`):** Runtime credentials (`NEWEBPAY_MERCHANT_ID`, `NEWEBPAY_HASH_KEY`, `NEWEBPAY_HASH_IV`, `NEWEBPAY_MPG_URL`, `PUBLIC_BASE_URL`) are stored in Cloudflare Worker **Variables and Secrets** (never in git). Setting `PUBLIC_BASE_URL` explicitly prevents `NotifyURL`/`ReturnURL` from resolving to the container's internal `localhost:8000`, and `NEWEBPAY_MPG_URL` targets sandbox (`https://ccore.newebpay.com/MPG/mpg_gateway`) first before promoting to production (`core.newebpay.com`).
* **Forfeited Alternatives:**
  * *Postpaid Monthly Invoicing:* Carrying uncollected Gemini token spend for unverified accounts risks runaway API bills; prepaid TWD packs + `402` cutoff bound liability cleanly.
  * *Collecting Raw Card Inputs in Frontend:* Hosted NewebPay MPG redirect keeps PCI-DSS scope out of the Angular/FastAPI stack.
  * *Whole-Worker Bypass under Zero Trust `Workers` Destination:* Would disable Cloudflare Access across the entire app; path-scoped **Public hostnames** restrict Bypass strictly to the two NewebPay callback paths.
