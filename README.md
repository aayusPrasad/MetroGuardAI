# MetroGuardAI

**AI-assisted Legal Metrology label inspection for Indian packaged commodities—from a product image or listing URL to a rule-cited compliance decision.**
[![SIH Problem Statement 26034](https://img.shields.io/badge/SIH-26034-FF671F?logo=india&logoColor=white)](https://www.sih.gov.in/) [![Python](https://img.shields.io/badge/Python-ML%2FAPI-3776AB?logo=python&logoColor=white)](model/) [![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi&logoColor=white)](model/main.py) [![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](src/) [![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14%2B-4169E1?logo=postgresql&logoColor=white)](Database/lmpc_compliance_schema.sql)

> **Repository status:** this is a hackathon prototype. The React dashboard currently uses in-memory mock data and client-side demo sessions; the FastAPI pipeline and PostgreSQL schema are present as separate implementation artifacts, but are not wired together yet. This README distinguishes implemented behavior from the intended persistence architecture.

## Overview

MetroGuard AI addresses the slow, inconsistent, and difficult-to-audit process of manually inspecting declarations on packaged commodities. It is designed for Legal Metrology enforcement officers working under India’s Ministry of Consumer Affairs and related state enforcement teams. Instead of reading each label by eye, an officer can inspect a controlled product image or an e-commerce listing and receive a field-level result with the applicable Legal Metrology (Packaged Commodities) Rules, 2011 (LMPC) reference.

For physical-pack analysis, the FastAPI prototype decodes an uploaded image, calibrates it using a 2 cm ArUco marker or manually supplied pack dimensions, locates/deskews a likely principal display panel (PDP), runs OCR, merges text into lines, extracts declarations with conservative regular expressions, and evaluates them with `ComplianceEngine`. The output is a structured result: `compliant`, `non_compliant`, or `review_required`, with field values, confidence, measured text height where calibration exists, violations, and rule citations. A PDF inspection certificate can also be generated.

For e-commerce analysis, the API fetches an HTTP(S) product URL, extracts visible page text, and falls back to up to three listing images if needed. Because a listing has no trustworthy physical scale or PDP geometry, the response explicitly limits its interpretation to declaration presence rather than physical font, placement, or PDP-area checks.

## Problem Statement — SIH 26034

SIH 26034 calls for a practical technology solution that helps Legal Metrology authorities check whether packaged commodities carry the declarations required by the LMPC Rules, 2011. The operational challenge is scale: officers must inspect large numbers of retail packages and online listings, while missing declarations, illegible text, incorrect pricing information, and inconsistent label presentation are time-consuming to identify manually.

MetroGuard AI’s proposed response is an officer-facing inspection workflow: capture or submit a label, extract the relevant declarations through computer vision/OCR, compare them with a structured rulebook, present traceable field-level outcomes, and route ambiguity to human review instead of silently guessing. The repository also includes a PostgreSQL schema for recording scans, declarations, violations, reports, users, and the rule data that drives an auditable deployment.

## Key Features

### Implemented API / ML pipeline

- **Physical image analysis:** `POST /analyze/image` accepts an uploaded image and optional `manual_pack_width_cm` / `manual_pack_height_cm` values.
- **E-commerce URL analysis:** `POST /analyze/ecommerce` fetches an HTTP(S) listing, parses visible HTML text, and falls back to the first three discovered images when text yields no declarations.
- **OCR and declaration extraction:** EasyOCR detections are confidence-filtered (default `0.25`), barcode-like strings are excluded, nearby words are merged into lines, and regex extractors identify consumer-care contact details, MRP, net quantity, manufacturing date, country of origin, manufacturer/packer/importer details, generic name, and unit sale price.
- **Conservative image preparation:** the OCR path upscales images narrower than 1500 px and applies CLAHE contrast enhancement. `model/image_enhancement.py` additionally provides a standalone ESPCN ×4 super-resolution helper with bicubic fallback, although `main.py` does **not** currently call that helper.
- **Scale-aware readability check:** calibrated physical scans return bounding-box text heights in millimetres. The current engine flags detected text below **1.0 mm** (ordinary packaging) or **2.0 mm** (`is_molded=True`) under Rule 7.
- **Three-state field results:** each active declaration is returned as `compliant`, `non_compliant`, or `not_detected`; an undetected active declaration makes the overall result `review_required`.
- **Rule-cited output:** every evaluated field carries `rule_ref`; font-height violations cite `Rule 7` and include a severity of `major`.
- **USP waiver logic:** the engine marks Unit Sale Price as exempt when PDP area is at most 100 cm² **and** MRP is at most ₹35; otherwise it requires USP. The response includes `usp_context.required` and a human-readable reason.
- **PDF certificate:** `POST /generate-report` returns `MetroGuard_Inspection_Report.pdf`, generated with ReportLab and containing calibration context, the audit trail, field status, rule reference, value/reason, and confidence/height when present.

### Implemented dashboard demonstration

- **Officer/admin dashboard UI:** React routes provide landing, login, dashboard, scan history/search/filtering, scan detail, image/e-commerce/batch-scan screens, review queue, analytics, and an admin-only user-management screen.
- **Batch-upload UI:** the UI accepts multiple file selection and advertises a 20-file batch; in the current demo it simulates processing and creates mock scan records rather than uploading files to FastAPI.
- **Human-in-the-loop review:** fields marked `not_detected` appear in the review queue. “Confirm Compliant” or “Confirm Violation” updates the in-memory record and, for a violation, adds a major enforcement item.
- **Analytics and history:** the dashboard renders severity, rule-reference, compliance-rate, manufacturer-ranking, search, status, and scan-type views from `src/mockData.ts`.
- **Demo access and roles:** officer/admin screens are conditionally shown by a `localStorage` session. This is a UI demonstration, not production authentication.

### Explicitly not implemented end-to-end yet

- The React app does **not** call the FastAPI endpoints; its scan/upload, reports, analytics, review queue, authentication, and roles use mock/client-side data.
- The API does **not** connect to PostgreSQL, authenticate users, persist scans, or expose CRUD/dashboard endpoints.
- The API produces PDF only. The dashboard’s “Download Report” creates a plain-text `.txt` mock report; there is no editable DOCX/report export implementation.
- The rulebook contains placement/PDP and detailed threshold tables, but the current `ComplianceEngine` only enforces the two general 1 mm / 2 mm font-height values—not the full table-based, placement, contrast, language, or scope-exclusion logic.

## System Architecture

```mermaid
flowchart LR
  U[Enforcement officer] --> FE[React 18 + Vite dashboard]
  FE -. current prototype: mockData.ts .-> M[In-memory scans and localStorage session]
  FE -. integration target .-> API[FastAPI]
  U -->|image / listing URL| API
  API --> P[Preprocessing\nOpenCV: calibration, PDP detection, deskew]
  P --> O[Extraction\nEasyOCR + line merging + regex]
  API -->|listing text or up to 3 images| O
  O --> C[ComplianceEngine\nLMPC rule references and statuses]
  C --> R[ReportLab PDF certificate]
  C -. planned persistence .-> DB[(PostgreSQL 14+\nrule + operational schema)]
```

### Pipeline stages

1. **Decode and calibrate.** Physical images are decoded with OpenCV. `preprocess_image` first looks for a `DICT_4X4_50` ArUco marker (default side: 2 cm); if it cannot find one, it can calculate scale from supplied pack width/height, assuming the pack fills the frame.
2. **Locate and orient the PDP.** A conservative rectangular-contour search estimates the PDP, excludes a detected marker, calculates area if a scale exists, and deskews the image. If no contour is safe to use, the full image is retained and area falls back to the calibrated frame area.
3. **Read and normalize text.** The extractor resizes/CLAHE-enhances the image, obtains EasyOCR detections, filters low-confidence text, removes likely barcodes, and merges aligned boxes into reading lines.
4. **Extract fields.** Regex and context-based routines extract values and their confidence/bounding-box measurements; unverified declarations remain `not_detected`.
5. **Apply rules and report.** `ComplianceEngine.evaluate` applies the active field checks, USP condition, and general font-height check, then emits the JSON report or a PDF certificate.

## Tech Stack

| Layer | Technologies actually used |
| --- | --- |
| Frontend | React 18, TypeScript, Vite 5, Recharts, Lucide React, Tailwind/PostCSS tooling |
| Backend API | Python, FastAPI, Pydantic, Starlette `FileResponse` |
| ML/CV | OpenCV (`cv2`, including ArUco and optional `dnn_superres`), NumPy, EasyOCR (lazy import), PyTorch/CUDA detection, regular expressions |
| Reporting | ReportLab |
| Database design | PostgreSQL 14+, `pgcrypto`, SQL enums, JSONB, GIN full-text index |

`@supabase/supabase-js` is installed in `package.json`, but no source file imports or configures it.

## Project Structure

```text
MetroGuardAI/
├── src/
│   ├── App.tsx                         # Single-file React dashboard, routes, demo login, views
│   ├── mockData.ts                     # In-memory scans, mock submission/review/search functions
│   ├── main.tsx                        # React entry point
│   ├── index.css                       # Dashboard styling
│   └── vite-env.d.ts                   # Vite TypeScript declarations
├── model/
│   ├── main.py                         # FastAPI image/e-commerce/report endpoints
│   ├── preprocessing.py                # ArUco/manual calibration, PDP detection, deskewing
│   ├── extraction.py                   # EasyOCR integration, line merging, regex extraction
│   ├── compliance_engine.py            # Field statuses, USP logic, general font-height validation
│   ├── image_enhancement.py            # Optional ESPCN ×4 super-resolution helper
│   ├── report_generator.py             # ReportLab PDF inspection certificate
│   ├── lmpc_rules_2011.json            # Structured LMPC rules and amendments reference
│   ├── test_pipeline.py                # Ad hoc TestClient script with an external Kaggle image path
│   └── model.code-workspace            # VS Code workspace settings
├── Database/
│   └── lmpc_compliance_schema.sql      # PostgreSQL DDL, indexes, and rulebook seed data
├── package.json                         # Frontend scripts and JavaScript dependencies
├── package-lock.json                    # Locked frontend dependency graph
├── vite.config.ts                       # Vite React configuration and @ alias
├── tailwind.config.js                   # Tailwind configuration
└── README.md                            # This documentation
```

## The Compliance Rulebook

[`model/lmpc_rules_2011.json`](model/lmpc_rules_2011.json) models the **Legal Metrology (Packaged Commodities) Rules, 2011 (as amended)**. It records scope exclusions (Rule 3 and Rule 26 cases), mandatory/conditional declarations, prohibited practices, Rule 7 font rules, Rule 8/9 placement and manner rules, e-commerce declarations, wholesale declarations, and Rule 32 penalties. It also records post-2021 country-of-origin/e-commerce additions and USP waiver conditions.

### Declaration definitions in the rulebook

| Field ID | Requirement | Rule reference | What it represents |
| --- | --- | --- | --- |
| `manufacturer_packer_importer` | mandatory | `Rule 6(1)(a), Rule 10` | Name and complete address of manufacturer, packer, or importer |
| `generic_name` | mandatory | `Rule 6(1)(b)` | Common/generic commodity name |
| `net_quantity` | mandatory | `Rule 6(1)(c), Rule 12, Rule 13` | Net quantity in standard unit |
| `mfg_month_year` | mandatory | `Rule 6(1)(d)` | Month/year of manufacture, pre-packing, or import |
| `mrp` | mandatory | `Rule 6(1)(e), Rule 2(m)` | Retail sale price inclusive of taxes |
| `dimensions` | conditional | `Rule 6(1)(f), Rule 14, Rule 15` | Dimensions where relevant to price |
| `country_of_origin` | conditional | `Amended provision (post-2021)` | Country of origin/manufacture for imported products |
| `consumer_care` | mandatory | `Rule 6(2)` | Complaint-contact details |
| `unit_sale_price` | mandatory | `Rule 2(m) / general practice (amended)` | Unit sale price, subject to waiver conditions |

**Current-engine mapping note:** `MANDATORY_FIELDS` in `model/compliance_engine.py` evaluates seven IDs: `manufacturer_packer_importer`, `generic_name`, `net_quantity`, `mrp`, `manufacturing_date`, `consumer_care`, and `unit_sale_price`. The date extractor outputs `manufacturing_date`, whereas the JSON’s corresponding rulebook ID is `mfg_month_year`; the engine supplies the fallback `Rule 6(1)(d)` reference. Conditional `dimensions` and `country_of_origin` are represented in the rulebook/extractor but are not part of that active engine tuple.

## Database Schema

[`Database/lmpc_compliance_schema.sql`](Database/lmpc_compliance_schema.sql) creates the `lmpc_compliance` database, enables `pgcrypto`, defines rule/operational enums, and seeds the rule data. It is a designed persistence layer; no Python module currently opens a database connection.

- **Rule-reference tables:** `rule_metadata`, `declaration_types`, `declaration_type_conditions`, `font_size_general_rules`, `font_size_thresholds`, `placement_rules`, `placement_manner_rules`, `prohibited_practices`, `scope_exclusions`, `ecommerce_declaration_fields`, `wholesale_declaration_fields`, and `penalties` normalize the JSON rulebook.
- **Operational tables:** `users` own uploads; `categories` classify `products`; a `product` is referenced by `scans`; each `scan` has many `declarations`, `violations`, and `reports`; `audit_log` records user actions.
- **Key relationships:** `products.category_id → categories.id`; `scans.product_id → products.id`; `scans.uploaded_by → users.id`; `declarations.scan_id → scans.id`; `violations.scan_id → scans.id`; `violations.declaration_id → declarations.id`; and `reports.scan_id → scans.id`. Declarations/violations also reference `declaration_types` where applicable.
- **Indexes:** scans-by-product, declarations-by-scan/type, violations-by-scan/type, product full-text search, and font-threshold table type are indexed.

## API Endpoints

These are the only FastAPI routes declared in `model/main.py`.

| Method | Path | Request | Behavior |
| --- | --- | --- | --- |
| `POST` | `/analyze/image` | multipart `file`; optional form `manual_pack_width_cm`, `manual_pack_height_cm` | Runs calibration, OCR/extraction, and compliance evaluation; returns JSON plus preprocessing metadata. |
| `POST` | `/analyze/ecommerce` | JSON `{ "url": "https://…" }` | Fetches listing text/images, extracts declarations, and returns a declaration-presence-oriented result plus an e-commerce limitation note. |
| `POST` | `/generate-report` | multipart `file`; optional form dimensions | Runs physical analysis and returns `MetroGuard_Inspection_Report.pdf`. |

The API is configured with `title="MetroGuard AI"` and `version="0.1.0"`. No CORS middleware, auth endpoint, database endpoint, or frontend API-base environment variable is implemented in this checkout.

## Getting Started

### Prerequisites

- **Node.js:** an actively supported Node LTS release (Node 20+ recommended for the Vite 5 frontend).
- **Python:** Python 3.11 is a practical target for the CV/OCR stack. The repository does not provide a `requirements.txt`, lockfile, or supported-Python declaration.
- **PostgreSQL 14+** only if you want to load the supplied schema; the current API will not use it.
- **Optional NVIDIA GPU/CUDA:** `extract_entities` checks `torch.cuda.is_available()` and gives EasyOCR that result. CPU operation is possible, but OCR may be slower.

### Run the dashboard (works from a clean checkout)

```bash
git clone <your-repository-url>
cd MetroGuardAI
npm ci
npm run dev
```

Open the local URL printed by Vite (normally `http://localhost:5173`). The UI has no API-base URL setting and does not require `.env` configuration because it uses `src/mockData.ts`.

### Demo credentials

| Role | Email | Password |
| --- | --- | --- |
| Officer | `officer@metroguard.gov.in` | `Officer@2026` |
| Admin | `admin@metroguard.gov.in` | `Admin@2026` |

These are convenience values handled in the browser. Any non-empty email/password can create a client-side session; the role is inferred from an `admin` email prefix or selected during sign-up. Do not treat these credentials or the UI role gate as authentication.

### Prepare and run the FastAPI prototype

There is no Python dependency manifest in the repository, so install the imports used by the source explicitly. Use **`opencv-contrib-python`**, not `opencv-python`, because marker calibration requires `cv2.aruco`; it also supplies the optional `dnn_superres` module used by the standalone enhancement helper.

> **Important source-export cleanup:** every Python module in `model/` and the JSON rulebook currently begins with a Jupyter `%%writefile …` line. That line is not valid Python/JSON, so a clean checkout cannot import the backend or parse the rulebook until it is removed. The commands below create a runnable copy under `/tmp`; they do not change tracked files.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install \
  fastapi "uvicorn[standard]" python-multipart pydantic \
  numpy opencv-contrib-python easyocr torch reportlab

rm -rf /tmp/metroguard-model
mkdir -p /tmp/metroguard-model
for file in model/*.py model/lmpc_rules_2011.json; do
  sed '1{/^%%writefile /d;}' "$file" > "/tmp/metroguard-model/$(basename "$file")"
done

cd /tmp/metroguard-model
LPMC_RULEBOOK_PATH=./lmpc_rules_2011.json uvicorn main:app --reload --port 8000
```

The API documentation will be available at `http://127.0.0.1:8000/docs`. The rulebook environment variable is intentionally spelled **`LPMC_RULEBOOK_PATH`** in code (not `LMPC_RULEBOOK_PATH`). The default is `lmpc_rules_2011.json` in the current working directory.

The first EasyOCR use may download OCR model assets. The optional ESPCN enhancement helper may also download `ESPCN_x4.pb` into the process working directory if called; the regular FastAPI image route currently does not invoke it.

### Load the PostgreSQL schema (optional)

The SQL file contains `CREATE DATABASE lmpc_compliance`, which must run outside a transaction and before the remaining statements. Run it in two passes:

```bash
# From the repository root: create the database from an admin connection.
sed -n '1,/^-- After this, connect to it before continuing:/p' \
  Database/lmpc_compliance_schema.sql | psql -U postgres -d postgres

# Then execute the extension, types, tables, indexes, and seed data.
sed '1,/^-- After this, connect to it before continuing:/d' \
  Database/lmpc_compliance_schema.sql | psql -U postgres -d lmpc_compliance
```

No database URL/environment variable is consumed by the current backend. Loading this schema prepares the intended data model only.

### Run both locally

1. Start the Vite dashboard with `npm run dev` from the repository root.
2. In a second terminal, run the cleaned FastAPI copy with Uvicorn as above.
3. Use the dashboard for the current UI demonstration and `http://127.0.0.1:8000/docs` or `curl` for real API analysis. They are separate in this revision—there is no frontend-to-backend bridge yet.

Example physical scan request:

```bash
curl -X POST http://127.0.0.1:8000/analyze/image \
  -F "file=@/absolute/path/to/package.jpg" \
  -F "manual_pack_width_cm=10" \
  -F "manual_pack_height_cm=15"
```

## Usage Walkthrough

### Dashboard demonstration

1. Start the frontend, open `/login`, and choose **Use** for either demo profile (or enter the credentials above).
2. Select **New Image Scan**, **Batch Upload**, or **E-Commerce Scan**. The image screen accepts a file-picker selection; the e-commerce form checks that the entered URL begins with `http://` or `https://`.
3. Start the scan and follow the simulated steps: Uploading, Calibrating, Extracting, Checking rules, and Generating report. The generated scan detail shows field statuses, values, confidence, mock rule references, and the USP context.
4. Open **Scan History** to search the in-memory scan list by product, manufacturer, or scan ID and filter by status/type.
5. Open **Review Queue**. For every `not_detected` field, select **Confirm Compliant** or **Confirm Violation**. The latter updates the mock scan to `non_compliant` and creates a major mock violation.
6. Use **Analytics** for the mock severity/rule/trend/manufacturer charts. Sign in as Admin to expose **User Management**.

### Real API demonstration

1. Run the cleaned FastAPI copy and open `/docs`.
2. Call `/analyze/image` with a clear package image. For meaningful millimetre measurements, include the default 2 cm ArUco marker or provide an accurate tightly framed pack width/height.
3. Read `overall_status`, `fields`, `violations`, `not_detected_fields`, and `usp_context`. A `not_detected` value is intentionally a review signal, not proof that a declaration is absent.
4. Call `/generate-report` using the same multipart input to download the PDF certificate.
5. For an online listing, call `/analyze/ecommerce` with `{ "url": "https://…" }`; use its returned note as a reminder that it cannot establish physical presentation compliance.

## Known Limitations and Design Decisions

- **Reject ambiguity rather than fabricate confidence.** The API returns 422 for undecodable images or when no confident text is found. The dashboard separately simulates a low-resolution rejection at 640 × 480 against its recommended 1280 × 720 capture guidance. Neither path should be interpreted as evidence that super-resolution can recover arbitrary low-quality source material.
- **Super-resolution is available but not in the live route.** `image_enhancement.py` can crop a likely PDP and attempt ESPCN ×4 enhancement, with bicubic fallback. It is not yet connected to `main.py`; the production-facing image endpoint instead uses resize + CLAHE before OCR.
- **Physical checks need a trustworthy scale.** E-commerce scans deliberately omit physical-scale claims. Their response says font size, placement, and PDP-area compliance cannot be assessed from a listing. Physical font measurements also remain unavailable unless marker/manual calibration succeeds.
- **Generic/common-name extraction is intrinsically weaker.** Unlike MRP, net quantity, and many address/contact declarations, labels have no consistent anchor keyword for a generic name. The extractor uses a residual-text heuristic, so this field should receive particular human scrutiny.
- **`is_molded` is a caller/engine flag, not image classification.** The engine can apply the 2 mm general threshold when `is_molded=True`, but no API parameter or CV classifier currently determines that flag automatically; FastAPI calls use the default `False`.
- **Rulebook breadth exceeds current enforcement breadth.** The JSON and SQL model detailed Rule 7 tables, placement/manner rules, exclusions, e-commerce fields, and penalties. The current `ComplianceEngine` hardcodes a narrower active field tuple and general font thresholds; it does not yet evaluate all conditions.
- **Rule currency requires legal validation.** The rulebook labels itself as the 2011 base rules with known 2021+ amendments and explicitly asks production users to cross-check the latest consolidated/amended text. It is an engineering reference, not a substitute for current legal review.
- **Prototype integration is intentionally incomplete.** Client sessions, roles, scans, review decisions, reports, and analytics are mock/in-memory. PostgreSQL is schema-only and FastAPI lacks auth/persistence/CORS integration.

## Future Roadmap

- Add automatic `is_molded` / printed-packaging classification and full table-based Rule 7 validation.
- Expand multilingual OCR while preserving reviewable field provenance.
- Replace client-side demo sessions with production-grade authentication, authorization, audit logging, and PostgreSQL-backed workflows.
- Integrate the dashboard with FastAPI and add controlled live ingestion/versioning of BIS/LMPC rule updates.

## Team / Credits

Replace these placeholders before submission:

- **[Aayush Prasad]** — Product / Solution Architecture
- **[Aayush Prasad]** — Computer Vision and OCR
- **[Arijit Roy]** — Backend and Compliance Rules
- **[Aayush Prasad]** — Frontend and UX
- **[Ranit Karmakar]** — Database / Testing / Documentation

## License

**Internal hackathon submission — not licensed for public distribution.**
