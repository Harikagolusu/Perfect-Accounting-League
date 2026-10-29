# PAL (Perfect Accounting League) - Codebase Analysis Summary

## 1. Current Architecture

### Frontend (TanStack Start + React 19)
- **Framework**: TanStack Start (React 19, React Router, TanStack Query)
- **UI Library**: Radix UI primitives + Tailwind CSS v4
- **State Management**: TanStack Query for server state, React Router for navigation
- **Port**: Serves on `localhost:8080` (CORS origin must match)
- **API Base**: `VITE_API_BASE_URL=http://localhost:8000` (from `.env.local`)
- **Key Routes**: Companies, Invoices, Bank Statements, Vouchers, Ledgers, Documents, Vendors

### Backend (FastAPI + SQLAlchemy 2.x + PostgreSQL)
- **Framework**: FastAPI with uvicorn
- **ORM**: SQLAlchemy 2.x with DeclarativeBase
- **Database**: PostgreSQL (psycopg v3 driver)
- **Migrations**: Alemcic with 16 revisions (head at `0011_add_phase1_accounting`)
- **Config**: pydantic-settings with `.env` file (single source of truth)
- **Authentication**: No auth layer yet; `actor` defaults to `"local-user"`
- **Rate limiting**: None built-in

### Key Directories
```
backend/
├── app/                      # FastAPI application
│   ├── api/v1/              # Versioned API routes (14 modules)
│   ├── core/                # Config, database, storage
│   ├── models/              # SQLAlchemy ORM models (12+ models)
│   ├── schemas/             # Pydantic v2 schemas
│   └── services/            # Business logic (30+ service modules)
│       ├── invoice/         # Invoice extraction & lifecycle
│       ├── bank/            # Bank statement processing
│       ├── voucher/         # Voucher creation, approval, posting
│       ├── ledger/          # Ledger master & suggestions
│       ├── accounting_mapping  # Explainable mappings
│       ├── ai/              # DeepSeek provider integration
│       └── exports/         # Workbook generation
├── alembic/                 # Database migrations (16 versions)
└── tests/                   # pytest suite (25+ test files)
```

### API Organization (v1)
All routes are nested under `/companies/{company_id}` for company-scoped operations, except:
- `GET /health`, `GET /api/v1/health`
- `POST /api/v1/companies`
- `GET /api/v1/companies` (list)
- `GET/POST/AI/` endpoints (top-level, not company-scoped)
- `GET /api/v1/tally/*` (Tally integration, top-level)

### 16 Alembic Migrations (chronological)
| Rev | Purpose |
|-----|---------|
| 0001 | Initial schema (empty baseline) |
| 0002 | Add companies table |
| 0003 | Company status enum (replaces is_active) |
| 0003_tally_sync_log | Tally sync log table |
| 0003_company_status | (duplicate/renamed) |
| 0003_extraction_fields | Extraction fields |
| 0004 | Add vendors |
| 0005 | Add documents |
| 0006 | Add invoices |
| 0007 | Add bank statements |
| 0008 | Add invoice source AI |
| 0009 | Add invoice vendor mapping |
| 0010 | Add client output fields |
| 0011 | Add phase 1 accounting (ledgers, vouchers, mappings) |
| 0012 | Bank Tally source |
| 0013 | Add audit entity types |

## 2. What Is Already Working

### Core CRUD Operations ✅
- **Companies**: Full CRUD with `ACTIVE`/`ONBOARDING`/`INACTIVE` status enum
- **Vendors**: Per-company scoped, with `ACTIVE`/`INACTIVE`/`NEEDS_MAPPING` status
- **Ledgers**: Company-scoped chart of accounts with unique (name, company) constraint
- **Bank Accounts**: With masked account numbers in responses
- **Documents**: Upload with content validation (signature + extension + MIME type)
- **Bank Statements**: Create, list, update, status transitions

### API Endpoints ✅
- Health checks (`/health`, `/api/v1/health`)
- Company management (create, list, get, update, soft-delete)
- Vendor master (CRUD per company)
- Ledger master (CRUD per company)
- Document inbox (upload, list, download, delete)
- Bank account management

### Invoice Processing Foundation ✅
- Document upload and validation
- Basic invoice creation from documents
- Invoice status lifecycle (DRAFT → PROCESSING → PROCESSED/NEEDS_REVIEW/FAILED)
- Vendor mapping workflow (UNMAPPED → SUGGESTED → MATCHED → CONFIRMED)
- Line item management per invoice
- Invoice export to XLSX workbook

### Voucher Workflow ✅
- Manual voucher creation
- Voucher from invoice (`create_voucher_from_invoice`)
- Voucher from bank transaction (`create_voucher_from_bank_transaction`)
- Voucher approval flow (DRAFT → SUBMITTED → APPROVED/REJECTED)
- Voucher rejection and reopen
- Voucher export to accounting workbook
- Voucher posting to TallyPrime (approved only)
- Voucher verification/verification of posting

### TallyPrime Integration ✅ (Read-only POC)
- Tally status check (`GET /tally/status`)
- Read companies from Tally (`GET /tally/companies`)
- Read ledgers from Tally (`GET /tally/ledgers`)
- Read vouchers from Tally (`GET /tally/vouchers`)
- Sync companies from Tally into PAL (`POST /tally/sync/companies`)
- Sync ledgers from Tally into PAL ledger master (`POST /tally/sync/{company_id}`)
- Sync bank accounts from Tally (`POST /tally/sync/{company_id}/bank-accounts`)
- Sync bank data (accounts + transactions) from Tally (`POST /tally/sync/{company_id}/bank-data`)
- Learn accounting mappings from Tally history (`POST /tally/sync/{company_id}/accounting-mappings`)

### AI Provider ✅ (DeepSeek foundation)
- AI status check (`GET /ai/status`)
- AI test endpoint (`POST /ai/test`)
- Invoice extraction using AI provider
- Bank statement extraction using AI provider
- Configurable via `.env` `deepseek_api_key`

### Tests ✅ (pytest suite)
- 25+ test files covering:
  - Companies, vendors, ledgers
  - Invoices and extraction
  - Bank statements and transactions
  - Vouchers and approval flow
  - Tally integration (some live tests)
  - OCR, regex, structured documents
  - Phase 1 accounting workflows
- Test suite runs against real PostgreSQL

## 3. Current Tally Flow

### Tally Read Path
1. **Status check**: `GET /tally/status` → checks connectivity to TallyPrime XML endpoint
2. **Read companies**: `GET /tally/companies` → lists companies in TallyPrime
3. **Read ledgers**: `GET /tally/ledgers` → reads ledger master from Tally
4. **Read vouchers**: `GET /tally/vouchers` → reads accounting vouchers from Tally
5. **Sync companies**: `POST /tally/sync/companies` → imports Tally companies into PAL master
6. **Sync ledgers**: `POST /tally/sync/{company_id}` → upserts Tally ledgers into PAL's company-ledger chart
7. **Sync bank accounts**: `POST /tally/sync/{company_id}/bank-accounts` → imports Tally bank account ledgers
8. **Sync bank data**: `POST /tally/sync/{company_id}/bank-data` → imports Tally bank accounts + bank-ledger vouchers
   - Creates bank accounts with placeholder numbers
   - Creates one statement per account for the requested period
   - Adds bank-side vouchers for every voucher touching those ledgers
   - Uses stable `external_id` to prevent duplicate vouchers on repeat sync
9. **Learn mappings**: `POST /tally/sync/{company_id}/accounting-mappings` → reads Tally's historical vouchers and creates explainable vendor→ledger mappings

### Tally Write Path (Single operation)
1. **Post voucher**: `POST /vouchers/{voucher_id}/post-to-tally`
   - Only APPROVED vouchers may be posted
   - Voucher XML built with `REMOTEID` = PAL's voucher id (prevents duplicates on retry)
   - Amounts and ledger names passed through exactly as approved (no recalculation)
   - Tally returns counts (created/altered/errors/exceptions)
   - On success: voucher → POSTED status with `posting_reference`
   - On failure: voucher → POSTING_FAILED, retryable
   - Already-posted voucher returns reference without contacting Tally

### Tally Integration Constraints
- **Read-only POC**: PAL never writes to Tally except via the single `post_to_tally` endpoint
- **URL server-side only**: `tally_base_url` from config, never from request
- **Timeout bounded**: 10-second max timeout (configurable 0-60s)
- **No proxy**: Tally URL never accepted from API callers
- **XML-over-HTTP**: TallyPrime's native XML-over-HTTP protocol (port 9000 default)

## 4. Current Invoice/Accounting Flow

### Invoice Processing Pipeline
```
1. Upload document → Document inbox (validated PDF/XLSX/DOCX/JPEG/PNG)
   ↓
2. Process document → AI extraction (if DeepSeek configured)
   ↓
3. Invoice created/reused → status DRAFT → PROCESSING → PROCESSED/NEEDS_REVIEW/FAILED
   ↓
4. Extracted values applied → invoice fields + line items
   ↓
5. Vendor mapping → UNMAPPED → SUGGESTED → MATCHED → CONFIRMED
   ↓
6. Invoice → PROCESSED (if mapping confirmed) or NEEDS_REVIEW (if mapping needs review)
```

### Vendor Mapping Detail
- **Auto-matching**: `match_vendor()` checks GSTIN/PAN/normalized name against Vendor Master
- **Suggestion**: When match found but `requires_confirmation=true`, becomes SUGGESTED
- **Confirmation**: Human confirms suggestion → vendor_id assigned, status → CONFIRMED
- **Reject**: `vendor_id=null` → status → REJECTED, never silently re-matched
- **Historical**: Last approved voucher's ledger recorded as mapping evidence

### Invoice Lifecycle Transitions
```
DRAFT → PROCESSING / NEEDS_REVIEW
PROCESSING → NEEDS_REVIEW / PROCESSED / FAILED
FAILED → PROCESSING (retry)
NEEDS_REVIEW → PROCESSING / PROCESSED (human resolved)
PROCESSED → (terminal, no exit without reopen)
```

### Voucher Creation Flows

#### From Invoice
```
invoice → suggest_for_invoice() → ledger suggestions
  → build voucher lines from invoice taxable/tax amounts + party ledger
  → create_manual_voucher() → DRAFT status
  → issues → NEEDS_REVIEW if problems
  → approve_voucher() → APPROVED status
  → post_to_tally() → Tally import
```

#### From Bank Transaction
```
transaction → suggest_for_bank_transaction() → ledger suggestions
  → determine voucher type: CONTRA/RECEIPT/PAYMENT
  → create_manual_voucher() → DRAFT status
  → approve_voucher() → APPROVED status
  → post_to_tally() → Tally import
```

#### Manual
```
VoucherCreate → create_manual_voucher() → DRAFT status
→ edit lines → submit_voucher() → PENDING_APPROVAL
→ approve_voucher() → APPROVED status
→ post_to_tally() or export workbook
```

### Voucher Status Flow
```
DRAFT → SUBMITTED_FOR_APPROVAL → APPROVED
      → NEEDS_REVIEW (if validation issues)
      → REJECTED (with reason)
      → DRAFT (reopen)
      → POSTING_IN_PROGRESS → POSTED / POSTING_FAILED
      → AWAITING_IMPORT_CONFIRMATION → POSTED / POSTING_FAILED
```

### Ledger Suggestion Hierarchy (explainable, never invents)
1. **Approved mapping** (most recent active mapping for vendor/party)
2. **Historical voucher** (last approved voucher for same vendor used this ledger)
3. **Configured vendor ledger** (`vendor.default_expense_ledger`)
4. **Configured company ledger** (`company.sales_ledger`, `company.cgst_ledger`, etc.)
5. **AI suggestion** (flagged `AI_SUGGESTED_LEDGER`, keeps voucher in NEEDS_REVIEW)
6. **No suggestion** → `NO_HISTORICAL_MAPPING` exception

### Accounting Mappings (explainable, audit-trail)
- Records evidence behind each ledger assignment
- `source_type`: INVOICE or BANK_TRANSACTION
- `match_type`: VENDOR, COUNTERPARTY, NARRATION, RULE
- `vendor_id` or `counterparty_name` links to identity evidence
- `ledger_id` points to the approved ledger
- `approved_by` / `approved_at` tracks who confirmed it
- `transaction_context`: `TALLY_HISTORY` (from Tally sync) vs manual
- **Never overwritten**: duplicates return existing mapping, updates are explicit

## 5. Important Files

### Backend Core
- `backend/app/core/config.py` - Settings, single source of truth
- `backend/app/core/database.py` - Engine, session, Base, get_db
- `backend/app/core/storage.py` - Document upload validation, filesystem safety
- `backend/backend/alembic/env.py` - Migration env config
- `backend/backend/alembic/versions/` - 16 migration files

### Models (ORM)
- `backend/app/models/company.py` - Company + CompanyStatus enum
- `backend/app/models/vendor.py` - Vendor + VendorStatus enum
- `backend/app/models/invoice.py` - Invoice + status/source/mapping enums
- `backend/app/models/bank.py` - BankAccount + BankStatement + BankTransaction
- `backend/app/models/ledger.py` - Ledger + LedgerSource enum
- `backend/app/models/accounting_mapping.py` - Explainable mapping evidence
- `backend/app/models/invoice_line_item.py` - Line items (canonical)
- `backend/app/models/voucher.py` - Voucher + VoucherType + VoucherSide + VoucherStatus
- `backend/app/models/tally_sync.py` - Tally sync log (append-only)

### API Routes (v1)
- `backend/app/api/v1/router.py` - Aggregate router, includes all modules
- `backend/app/api/v1/companies/router.py` - Company CRUD
- `backend/app/api/v1/invoices/router.py` - Full invoice processing
- `backend/app/api/v1/bank/router.py` - Bank accounts, statements, transactions
- `backend/app/api/v1/vouchers/router.py` - Draft/approval/export/posting
- `backend/app/api/v1/ledgers/router.py` - Ledger master CRUD
- `backend/app/api/v1/documents/router.py` - Document inbox
- `backend/app/api/v1/vendors/router.py` - Vendor master
- `backend/app/api/v1/tally/router.py` - Tally integration
- `backend/app/api/v1/ai/router.py` - AI provider status/tests

### Services (Business Logic)
- `backend/app/services/invoice.py` - Invoice CRUD, extraction lifecycle, vendor mapping
- `backend/app/services/bank.py` - Bank accounts, statements, transactions, processing
- `backend/app/services/voucher.py` - Voucher state machine, approval, posting
- `backend/app/services/voucher_factory.py` - Build vouchers from invoices/bank txns
- `backend/app/services/voucher_posting.py` - TallyPrime direct posting
- `backend/app/services/voucher_validation.py` - Voucher validation rules
- `backend/app/services/voucher_export.py` - Accounting workbook export
- `backend/app/services/ledger_suggestion.py` - Explainable ledger suggestions
- `backend/app/services/accounting_mapping.py` - Mapping CRUD + evidence tracking
- `backend/app/services/invoice_extraction.py` - AI extraction pipeline
- `backend/app/services/bank_extraction.py` - AI bank statement extraction
- `backend/app/services/bank_validation.py` - Bank validation rules
- `backend/app/services/ai.py` - DeepSeek provider integration
- `backend/app/services/posting_period.py` - Tally posting date resolution

### Tally Integration
- `backend/app/integrations/tally/service.py` - TallyService facade
- `backend/app/integrations/tally/client.py` - HTTP client for XML-over-HTTP
- `backend/app/integrations/tally/xml.py` - XML request/response builders
- `backend/app/integrations/tally/parser.py` - Safe XML parsers
- `backend/app/integrations/tally/exceptions.py` - Tally error types

### Key Schemas
- `backend/app/schemas/invoice.py` - InvoiceCreate, InvoiceResponse, ProcessInvoiceResponse
- `backend/app/schemas/bank.py` - BankAccount, BankStatement, BankTransaction schemas
- `backend/app/schemas/voucher.py` - VoucherCreate, VoucherResponse, VoucherPostResponse
- `backend/app/schemas/common.py` - NormalisedTaxIds, NormalisedEmail, TimestampedResponse

## 6. Known Issues / Unfinished Work

### Phase 1 - Not Fully Completed
- **GST/TDS calculations**: No tax rate logic, no place-of-supply derivation
- **TDS deductions**: Not implemented
- **GSTR-2B reconciliation**: Not started (per README)
- **AI provider not configured by default**: `deepseek_api_key` must be set in `.env`
- **OCR not compiled**: Tesseract optional, falls back to vision provider

### Missing Features (Per README "Not Started")
- **Vendors, invoices, bank statements, GST, TDS, reconciliation**: Marked "Not started" in README status table
- **Authentication/authorization**: No auth layer; `actor` defaults to `"local-user"`
- **GST/TDS workings**: Not implemented
- **Zoho Books integration**: Listed as future work, not implemented
- **Bank reconciliation against GSTR-2B**: Not started

### Tally Integration Limitations
- **Read-only POC**: Single write path (`post_to_tally`), no bidirectional sync
- **No company mapping**: PAL companies not automatically mapped to Tally companies
- **Ledger name matching**: Relies on exact string match; no fuzzy mapping
- **No inventory/voucher items mode**: Tally ISINVOICE=No enforced, inventory mode not supported
- **Bank reconciliation**: Tally sync creates statements but full reconciliation not implemented

### Invoice Extraction
- **AI dependency**: Extraction fails if DeepSeek not configured
- **Page cap**: Max 10 pages per document (configurable via `invoice_max_pages`)
- **Text limit**: 120,000 characters max sent to vision model
- **No local fallback**: If AI unavailable, invoice must be entered manually

### Bank Statement Extraction
- **AI dependency**: Same as invoice extraction
- **No OCR fallback path documented** beyond what's in the code
- **20-page max** for PDF statements

### voucher_posting.py
- **Tally connectivity**: Requires running TallyPrime instance on configured URL
- **No offline mode**: Must connect to Tally to post
- **No retries with backoff**: Single attempt per call

## 7. Safest Next Development Step

### Recommended Priority

Based on the codebase analysis, the safest next development step is:

#### **1. Configure DeepSeek AI Provider**
- Add `DEEPSEEK_API_KEY` to `.env` file
- This enables:
  - Invoice extraction from PDFs/XLSX/DOCX
  - Bank statement parsing
  - AI-ledger suggestions
  - Processing pipeline activation

#### **2. Test Phase 1 Accounting Flow End-to-End**
- Create a company → add ledgers → upload invoice document → process → approve voucher → post to Tally
- Verify all status transitions work correctly
- Test vendor mapping workflow

#### **3. Complete Missing Ledger Configurations**
- Ensure companies have `sales_ledger`, `cgst_ledger`, `sgst_ledger`, `igst_ledger`, `cess_ledger`, `round_off_ledger` configured
- These are referenced by the voucher validation and suggestion engine

#### **4. Add Bank Account Ledger Names**
- Bank accounts need `ledger_name` configured for Tally sync and voucher creation
- Currently optional but required for smooth bank-to-voucher flow

#### **5. Write Tests for New Features**
- The test suite exists but focuses on foundation layers
- Add tests for: invoice extraction, bank processing, voucher approval, Tally posting

### Minimal Implementation Path
```
Step 1: Set DEEPSEEK_API_KEY in .env
Step 2: Create test company + ledgers via API
Step 3: Upload a test invoice PDF
Step 4: Run process-document → verify extraction
Step 5: Confirm vendor mapping → invoice → voucher → approve → post-to-tally
Step 6: Add bank account + statement → process → voucher → post-to-tally
```

This path validates the entire pipeline from document upload through Tally posting with minimal code changes (mostly configuration + integration testing).
```

### Quick Start Checklist
- [ ] Copy `.env.example` to `.env`, add `DEEPSEEK_API_KEY`
- [ ] Set `tally_base_url` if TallyPrime is available (defaults to `http://localhost:9000`)
- [ ] Create company with ledgers via `POST /api/v1/companies`
- [ ] Upload invoice document via `POST /api/v1/companies/{id}/documents`
- [ ] Run `POST /api/v1/companies/{id}/invoices/process-document/{doc_id}`
- [ ] Verify vendor mapping suggestions
- [ ] Create voucher from invoice → submit → approve
- [ ] Post to Tally: `POST /vouchers/{id}/post-to-tally`