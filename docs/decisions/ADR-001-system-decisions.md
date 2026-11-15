<!-- cSpell:words JSONB pgvector PyMuPDF Docling Qdrant asyncpg MOAOPD PesticideGropty PesticideBrcode PesticideLicImage APHIA MinerU Reducto Surya agrochemical MSDSs Pydantic WebP webp ymin xmin ymax xmax httpx Streamlit Gradio VertexAI fitz -->
# ADR-001: Core System Architecture & Tooling Decisions

## Status
Accepted

## Date
2026-11-15

## Context
Regulatory Affairs (RA) teams managing Taiwan agrochemical and specialty chemical registrations (~10–500 products) must cross-reference English/bilingual global documents (notarized factory/source-change certificates, 16-section GHS MSDSs) against Traditional Chinese local records (Taiwan MOA `EIR027` registry data, product label PDFs exported from graphic design software, and official government letters 公文).

Every compliance finding must carry exact spatial provenance (`[doc_id, page, box_2d]`) so an RA specialist can click any recommended label update and visually verify the source clause or label field. Additionally, the repository is public while local runs handle confidential certificates and Paid-tier API credentials.

## Decision

1. **Database Layer — Single PostgreSQL 16 (`JSONB`):**
   * Store cached MOA registry snapshots, extracted document schemas (`GroundedField[T]`), normalized page dimensions, and flattened document clauses (`box_2d`) in a single PostgreSQL 16 database using `JSONB` and canonical join keys (`reg_no`, `common_name`, `pesticide_code`, `company_name`, `doc_type`).
   * Construct database connections via `SQLAlchemy URL.create()` from discrete `POSTGRES_*` variables in `.env` so special characters in passwords and host-vs-Docker overrides (`POSTGRES_HOST=db`) never conflict.
2. **Government Registry Ingestion — On-Call `httpx` Lookup Against Official MOA `EIR027` OpenData API:**
   * Rather than bulk-downloading thousands of permits on a weekly cron schedule, retrieve the target product's MOA registry data **on-call** (`GET /api/registry/{reg_no}` or inside `get_product_baseline`) directly from `https://data.moa.gov.tw/Service/OpenData/FromM/PesticideData.aspx?$filter=...` using UTF-8 URL-encoded `$filter` (e.g., by `許可證號` or `中文名稱`).
   * For the matched product (`reg_no = 許可證字 + 許可證號`), concurrently fetch its linked JSON sub-endpoints (`PesticideDetail.aspx`, `PesticideLicImage.aspx`, `PesticideBrcode.aspx`, `PesticideGropty.aspx`) and upsert the snapshot into `moa_registry` (`ON CONFLICT (reg_no) DO UPDATE`, falling back to the cached PostgreSQL row if the external MOA API is temporarily unreachable).
3. **Document Pre-Processing & Parsing — In-RAM `PyMuPDF` + `Pillow` + `Gemini 2.5 Flash / Flash-Lite` Constrained Decoding:**
   * Because product labels are graphic-design PDFs (Illustrator/InDesign exports) alongside PDF MSDSs, certificates, and 公文, read all uploaded PDFs strictly in RAM (`fitz.open(stream=pdf_bytes, filetype="pdf")`) and discard raw PDF bytes immediately after rendering pages to `<= 1600px` `.webp` images (`quality=85`) under `{STORAGE_DIR}/pages/{doc_id}/`.
   * Serve page images through `/api/documents/{doc_id}/pages/{page}/image` so Nginx reverse-proxying (`/api/*` → `http://backend:8000`) works identically in Docker Compose and local CLI dev.
   * Feed interleaved page batches (`["=== PAGE {global_page_no} ===", img, ...]`, up to 8 pages per call or 5-page windows for longer MSDSs) to `Gemini 2.5 Flash / Flash-Lite` with `response_mime_type="application/json"` and `response_schema`.
   * For multi-window extraction, top-level section fields are nullable (`GroundedField[T] | None = None`) and list fields default to `[]` so constrained decoding never forces hallucination of sections outside the current window; window results merge deterministically by accepting the first non-null field whose `page` falls within the window's `[start_page, end_page]` bounds and concatenating/deduplicating list items.
   * Enforce **Chain-of-Grounding (`GroundedField[T]`)** with fields declared in strict autoregressive order (`verbatim_text` → `value` → `page` → `box_2d`) plus Pydantic post-validators enforcing 4-tuple `[ymin, xmin, ymax, xmax]` bounds (`0 <= coord <= 1000`, `ymin < ymax`, `xmin < xmax`) and `1 <= page <= page_count`.
4. **Agentic Compliance Copilot — Two-Phase Execution (`Gemini 2.5 Pro / Flash`):**
   * **Phase 1 (Tool Loop):** Execute 4 deterministic/SQL function-calling tools (`inspect_uploaded_doc`, `get_product_baseline`, `search_regulatory_history`, `run_cascade_diff`) without `response_schema`. Baseline queries (`get_product_baseline` and `run_cascade_diff`) explicitly exclude the candidate `uploaded_doc_id` (`is_active_baseline = TRUE AND doc_id != uploaded_doc_id`) to prevent self-diffing when auditing a newly uploaded MSDS or label.
   * **Phase 2 (Constrained Synthesis):** Detach tools and enforce `response_schema=ComplianceAuditReport` via constrained decoding, validating that every cited `(doc_id, page)` exists in the Phase 1 tool context before returning the strongly-typed JSON (`section_1_document_evidence` and `section_2_label_update_checklist`) to the frontend.
5. **Frontend Stack — Angular 19 + Angular Material + HTML5 `<canvas>` Overlay:**
   * Render the audit workspace as a split-pane Angular Material interface where clicking any `[doc_id, page, box_2d]` citation updates an Angular Signal, loads `/api/documents/{doc_id}/pages/{page}/image`, and draws the scaled `[ymin, xmin, ymax, xmax]` (`0–1000`) bounding box onto an HTML5 `<canvas>` layered over the `.webp` page image.

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

### 6. Streamlit / Gradio or React Frontend
* **Pros:** Streamlit/Gradio allow rapid Python-only prototyping.
* **Cons:** Streamlit re-runs the Python script on every interaction, making instant click-to-highlight `<canvas>` bounding-box overlays sluggish; team preference and enterprise frontend standard is Angular + Angular Material.
* **Rejected:** Angular 19 + Angular Material with Signals and HTML5 `<canvas>` renders `[ymin, xmin, ymax, xmax]` highlights in `<5ms` with strict TypeScript contract parity.

## Consequences
* **Positive:** Zero dual-database synchronization bugs; zero background cron schedulers (`APScheduler` eliminated); zero headless browser or local GPU dependencies; `100%` recall on uploaded certificates via SQL `doc_id` filtering; raw confidential PDFs are never persisted to disk; every extracted field and Copilot checklist item is guaranteed by token-level constrained decoding + Pydantic bounds validators to carry valid `[doc_id, page, box_2d]`.
* **Trade-offs & Mitigations:**
  * *Paid-Tier API Requirement:* `GEMINI_API_KEY` must belong to a billing-enabled (Paid tier) Google AI Studio project (or `GOOGLE_GENAI_USE_VERTEXAI=true`) so confidential documents are excluded from model training.
  * *Page-Image Lifecycle:* While raw PDFs are processed purely in RAM, rendered `.webp` page images are stored in `{STORAGE_DIR}/pages/{doc_id}/` and served via `/api/documents/{doc_id}/pages/{page}/image` so the Angular `<canvas>` viewer can display the document background behind `box_2d` highlights; deleting a document via the API/UI purges its `{STORAGE_DIR}/pages/{doc_id}/` directory alongside its PostgreSQL rows.
