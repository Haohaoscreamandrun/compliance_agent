# Compliance Agent

Full-stack regulatory compliance system (**FastAPI** + **Angular / Angular Material** + **PostgreSQL 16 `JSONB`**) for agrochemical and specialty chemical Regulatory Affairs (RA) teams (~10–500 registered products). It cross-references English/bilingual global documents (notarized factory/source-change certificates, 16-section GHS MSDSs) against Traditional Chinese local baselines (Taiwan MOA `EIR027` registry records, product labels, and official government letters 公文) to produce a field-by-field **Label Update Checklist** with interactive `[doc_id, page, box_2d]` visual highlights on the source documents.

---

## Quick Start

1. **Clone the repository and switch to `develop`:**

   ```bash
   git clone <repo-url>
   cd compliance_agent
   git checkout develop
   ```

2. **Configure environment variables (`.env` is git-ignored and never pushed):**
   Create a local `.env` file in the repository root with `GEMINI_API_KEY`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`.
   * **Data Privacy Requirement:** Ensure the Google AI Studio project tied to `GEMINI_API_KEY` has **Pay-as-you-go billing enabled (Paid tier)** before uploading confidential certificates or unpublished MSDSs so inputs are excluded from model training.
   * **Optional GCP Vertex AI Mode:** Set `GOOGLE_GENAI_USE_VERTEXAI=true`, set `GOOGLE_CLOUD_PROJECT`, and place any local service-account key inside `./secrets/` (git-ignored). When `GOOGLE_GENAI_USE_VERTEXAI=true`, the backend ignores `GEMINI_API_KEY` to avoid `google-genai` SDK mutual-exclusion errors.
3. **Start with Docker Compose (once Component 1–5 land):**

   ```bash
   docker compose up --build
   ```

   * FastAPI Backend & OpenAPI docs: `http://localhost:8000/docs`
   * Angular + Angular Material UI: `http://localhost:4200`

---

## Commands

| Command | Description |
| :--- | :--- |
| `docker compose up --build` | Start PostgreSQL 16 (`db`), FastAPI (`backend`), and Angular (`frontend`) |
| `cd backend && pytest -v` | Run backend unit and schema/diff/copilot tests |
| `cd backend && uvicorn app.main:app --reload` | Start FastAPI dev server on `BACKEND_HOST:BACKEND_PORT` (`127.0.0.1:8000`) |
| `cd frontend && npm start` | Start Angular dev server on `http://localhost:4200` |
| `cd frontend && npm run build` | Verify production Angular + Angular Material build and TypeScript types |
| `git check-ignore -v <path>` | Verify `.gitignore` blocks confidential RA documents and secrets |

---

## Architecture

### 1. Repository Structure

```text
compliance_agent/
├── .gitignore                      # Public-repo guardrails (blocks all .env*, /secrets/, and confidential RA docs)
├── .gitattributes                  # Enforces clean LF line endings across Windows/Linux containers
├── docker-compose.yml              # Container orchestration (PostgreSQL 16, FastAPI, Angular)
├── README.md                       # Public project overview, commands, architecture, and workflow
├── docs/
│   ├── intent/
│   │   └── label-compliance-mvp.md # Confirmed Statement of Intent
│   └── decisions/
│       └── ADR-001-system-decisions.md # Architecture Decision Record & forfeited alternatives
├── tasks/
│   └── todo.md                     # Ordered small-CL task tracker across Components 1–5
├── backend/                        # Python 3.12 + FastAPI + SQLAlchemy 2.0 (asyncpg) + google-genai
│   ├── pyproject.toml
│   ├── app/
│   │   ├── config.py               # pydantic-settings v2 loader + SQLAlchemy URL.create() builder
│   │   ├── main.py                 # FastAPI app, CORS, static page-image route
│   │   ├── db/                     # Single-store PostgreSQL 16 JSONB models & session
│   │   ├── schemas/                # GroundedField[T], 4 document extraction schemas, ComplianceAuditReport
│   │   ├── prompts/                # Domain extraction & Two-Phase Copilot system prompts
│   │   ├── services/               # PyMuPDF+Pillow PDF preprocessor, Gemini parser, on-call MOA EIR027 client, cascade diff
│   │   ├── agent/                  # 4 Function-Calling tools & Two-Phase Copilot runner
│   │   └── api/                    # REST routes (/api/registry, /api/documents, /api/copilot/audit)
│   └── tests/                      # Pytest suite + synthetic test fixtures in tests/fixtures/
└── frontend/                       # Angular 19 + Angular Material split-pane workspace
    └── src/app/
        ├── models/                 # TypeScript interfaces mirroring backend Pydantic schemas
        ├── services/               # HttpClient service for FastAPI endpoints
        └── components/
            ├── registry-toolbar/   # Permit selector (許可證字號) & on-demand MOA EIR027 lookup
            ├── copilot-audit-panel/# Section 1 (Document Evidence chips) & Section 2 (MatTable Checklist)
            └── bbox-svg-viewer/    # Inline <svg viewBox="0 0 1000 1000"> overlay rendering [ymin, xmin, ymax, xmax] boxes
```

### 2. Core Pipeline & Tooling

1. **Taiwan MOA `EIR027` On-Call Registry Lookup (`httpx` + PostgreSQL Cache):**
   * When querying or auditing a product (`reg_no = 許可證字 + 許可證號`, e.g., `農藥製00001`), queries the official MOA OpenData endpoint (`https://data.moa.gov.tw/Service/OpenData/FromM/PesticideData.aspx`) on-call using UTF-8 URL-encoded `$filter`.
   * Concurrently fetches the 4 linked JSON sub-endpoints for that product (`農藥使用範圍` `PesticideDetail.aspx`, `許可證標示` `PesticideLicImage.aspx`, `農藥條碼資料` `PesticideBrcode.aspx`, `混合劑百分率` `PesticideGropty.aspx`) and caches the snapshot in `moa_registry` via `ON CONFLICT (reg_no) DO UPDATE` (serving as an offline fallback if the MOA API is temporarily unreachable).
2. **Pre-Processing & Constrained Decoding Document Parser (`PyMuPDF` + `Pillow` + `Gemini 2.5 Flash / Flash-Lite`):**
   * Reads uploaded PDFs (graphic-design exported label PDFs, MSDSs, certificates, and 公文) directly in RAM via `PyMuPDF` (`fitz`) and renders pages to normalized `.webp` images (longest edge `<= 1600px`, quality `85`).
   * Passes interleaved page batches (`["=== PAGE 1 ===", img_1, "=== PAGE 2 ===", img_2, ...]`, up to 8 pages per call or 5-page windows for 16-section MSDSs) with `response_mime_type="application/json"` and `response_schema`.
   * Enforces **Chain-of-Grounding (`GroundedField[T]`)** ordered `verbatim_text` → `value` → `page` → `box_2d` (`[ymin, xmin, ymax, xmax]` normalized `0–1000`) across 4 specialized schemas:
     * `ProductLabelExtraction` (Traditional Chinese label images/PDFs + visual `GHS01–GHS09` hazard diamonds + APHIA drawn modification instructions)
     * `NotarizedCertExtraction` (new-state LoA or Certificate of Registration facts, authorizing/authorized entities, product code/dosage/formulation, issuing vs. factory state)
     * `MsdsExtraction` (繁體中文/簡體中文/English MSDSs linked via `common_name` + `dosage_and_content` + `formulation_type`; latest `issued_version`, Section 2 `GHS01–GHS09` pictograms, and hazard statements)
     * `OfficialLetterExtraction` (Taiwan 公文 `發文字號`, ROC-to-ISO `發文日期`, `affected_reg_nos`, `chinese_common_names`, `dosage_and_formulation`, deadlines, and mandates)
3. **Two-Phase Agentic Compliance Copilot (`Gemini 2.5 Pro / Flash`):**
   * **Phase 1 — Autonomous Function-Calling Loop:** Executes 4 deterministic/SQL tools against PostgreSQL `JSONB`:
     1. `inspect_uploaded_doc(doc_id, focus_topic)`: Retrieves all extracted legal clauses and `box_2d` coordinates for the uploaded document (`100%` recall without top-$k$ truncation).
     2. `get_product_baseline(reg_no, common_name, company_name)`: Fetches active product label (with field-level `box_2d`), latest MSDS, and synced MOA `EIR027` registry baseline.
     3. `search_regulatory_history(reg_no, common_name, topic)`: Searches historical official letters (公文) and compliance deadlines with `box_2d`.
     4. `run_cascade_diff(reg_no, uploaded_doc_id)`: Deterministic Python comparator checking (a) factory & entity/emergency contact alignment, (b) GHS pictogram/signal word/H-P statement drift, (c) MOA registry sync status (`pending_moa_approval` vs. synced), and (d) open official letter mandates.
   * **Phase 2 — Constrained Synthesis:** Detaches tools and enforces `response_schema=ComplianceAuditReport` via token-level grammar masking so the Angular Material UI receives guaranteed JSON containing `section_1_document_evidence` and `section_2_label_update_checklist`.

### 3. Technology Decisions & Why Alternative Choices Were Forfeited

| Subsystem | Chosen Tool / Pattern | Forfeited Alternatives | Rationale |
| :--- | :--- | :--- | :--- |
| **Database** | **Single PostgreSQL 16 (`JSONB`)**<br>*(in-place `pgvector` upgrade path deferred)* | **Qdrant Cloud** + Dual SQL/Vector store; Vector-first RAG on single docs | Avoids dual-store split-brain bugs on document update/delete. Cross-language matching (`Carbendazim` ↔ `貝芬替`) depends on canonical join keys (`reg_no`, `common_name`, CAS), and 2–15 page certificates require `100%` clause recall via `WHERE doc_id = $1` rather than top-$k$ vector approximation. |
| **Document Parsing & BBoxes** | **Unified `PyMuPDF` + `Pillow` + `Gemini Flash / Flash-Lite`** (`response_schema` + native `box_2d` `[0–1000]`) | **IBM Docling**, **Mistral OCR**, **DocLayout-YOLO**, **RapidOCR**, **MinerU**, **Marker** | Eliminates coordinate-system mismatches across multiple OCR engines and multi-GB local model weights. Native VLM constrained decoding handles Taiwan 公文 red seals, ROC dates (`民國年`), and visual `GHS01–GHS09` hazard pictograms in one pass. |
| **Gov Registry Ingestion** | **On-Call `httpx` Lookup + Official MOA `EIR027` OpenData API** (with PostgreSQL snapshot cache) | **`Crawl4AI` / `Playwright`** scraping; Bulk weekly background cron jobs (`APScheduler`) | `MOAOPD-API-EIR027` natively supports `$filter` and 4 JSON sub-endpoints (`PesticideDetail`, `PesticideLicImage`, `PesticideBrcode`, `PesticideGropty`), so fetching only the target permit on-call takes `<1s` and eliminates background schedulers and headless browsers. |
| **Prompt & Output Control** | **`GroundedField[T]` Chain-of-Grounding** + **Two-Phase Copilot** | Single-turn `tools` + `response_schema`; Raw PDF binary dumps; Free-text Markdown tables | Autoregressive field ordering (`verbatim_text` → `value` → `page` → `box_2d`) grounds bounding boxes to transcribed tokens. Separating Phase 1 (Tools) from Phase 2 (Constrained JSON) avoids Gemini API tool/schema conflicts. |
| **Frontend UI** | **Angular 19 + Angular Material** + Inline `<svg viewBox="0 0 1000 1000">` overlay | Raw HTML5 `<canvas>` pixel math, **React / Next.js**, **Streamlit**, **Gradio** | Maps Gemini's `0–1000` `box_2d` coordinates 1:1 in vector SVG without JS resize/DPI math or volatile `canvas-html` features, with `fetchpriority="high"`, `@container` queries, and WCAG 2.1 AA bilingual (`zh-Hant`/`en`) accessibility. |

---

## Contributing & Git Workflow

* **Branching Model:**
  * `main`: Stable, review-gated baseline.
  * `develop`: Active development branch. All work lands on `develop` in small, single-concern CLs (~60–200 lines) and merges to `main` via Pull Request at each component checkpoint in [tasks/todo.md](file:///c:/Users/J8426/文件/GitHub/compliance_agent/tasks/todo.md).
* **Doubt-Driven Review Gate:** Every non-trivial CL undergoes a fresh-context adversarial review (`CLAIM` → `EXTRACT` → `DOUBT` → `RECONCILE` → `STOP`) and pauses for human review before advancing to the next CL.
* **Public Repository Secret & Document Hygiene:**
  * Never commit `.env`, API keys, or GCP service account JSON files (place local GCP keys in `./secrets/`).
  * Real company certificates, MSDSs, label scans, and 公文 (`*.pdf`, `*.webp`, `*.png`, `*.jpg`, `*.odt`, `*.di`, `*.xlsx`, `*.csv`, `*.zip`) are blocked globally by [.gitignore](file:///c:/Users/J8426/文件/GitHub/compliance_agent/.gitignore); only synthetic test fixtures under `backend/tests/fixtures/` may be tracked.
