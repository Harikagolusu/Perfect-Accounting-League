# PAL — Session Handoff

**Date:** 2026-10-30
**Area:** Document Inbox extraction flow + Invoice Processing accounting voucher
**Branch:** `main` (connected to Lovable — do not rewrite pushed history)

---

## Project at a glance

PAL is a company-scoped invoice/accounting tool.

- **Backend:** FastAPI + SQLAlchemy (PostgreSQL), under `backend/app`. Services in `backend/app/services`, routes in `backend/app/api/v1`.
- **Frontend:** React + TanStack Router/Query + Vite, under `src`. Routes in `src/routes`, API clients in `src/lib/api`, query hooks in `src/lib/queries`, shared UI in `src/components/pal`.
- **AI extraction:** DeepSeek provider in `backend/app/services/ai`, invoice extraction in `backend/app/services/invoice_extraction.py`.
- Every record is company-scoped (`companyId` on the model, URL-scoped on the API). Colours come only from tokens in `src/styles.css`. No seeded/sample data — modules render empty states when the backend has none.

## Intended workflow (as settled this session)

1. **Document Inbox = upload + invoice extraction only.** User picks Company + Financial Year/Period, uploads up to 10 files (`MAX_BATCH = 10`), clicks **Upload Files**. Extraction then runs automatically, one document at a time.
2. A dedicated live progress panel shows every uploaded document as a row: spinner + **Extracting…** for the one in flight, **✓ Completed** when its fields are read, **Needs review** / **Failed** otherwise, plus an overall **X/N completed · NN%** bar.
3. The page does **not** redirect to Invoice Processing during or after extraction.
4. **Invoice Processing = review, vendor/ledger mapping, accounting voucher, approval, Tally posting.** Its existing workflow was kept intact.

## What was built this session

### Backend auto-voucher during inbox extraction
- `backend/app/services/invoice.py`
  - `process_invoice(..., prepare_voucher: bool = False)` — new opt-in flag. When set, after a document's fields are read it calls `_prepare_invoice_voucher(db, invoice)`.
  - `_prepare_invoice_voucher` builds and saves the draft accounting voucher via `create_voucher_from_invoice` (idempotent — one voucher per invoice; ledgers auto-mapped from Tally history by `suggest_for_invoice`). It **never gates** the invoice status and never raises (a mapping/date gap just leaves a draft or no voucher).
  - Voucher is prepared on both `PROCESSED` and `NEEDS_REVIEW` extraction outcomes, so every **dated** invoice gets its voucher. A clean extraction stays `PROCESSED`.
  - `process_document(..., prepare_voucher=False)` forwards the flag.
- `backend/app/api/v1/invoices/router.py` — `POST /process-document/{document_id}` gained a `prepare_voucher` query param (default `false`), so the plain extraction endpoint is unchanged for its existing tests.
- `backend/tests/test_invoice_extraction.py` — `test_inbox_prepare_voucher_saves_a_draft_voucher_without_gating_status`: asserts a clean read stays `PROCESSED`, exactly one voucher is saved, and a rerun does not create a second (idempotent).

### Frontend
- `src/lib/api/invoices.ts` — `processDocument(companyId, documentId, prepareVoucher = false)` appends `?prepare_voucher=true`.
- `src/lib/queries/invoices.ts` — `useProcessDocumentsBatch`: processes documents one by one; per-document `try/catch` so one failure does not abort the batch; optimistic `setDocStatus` writes each row's status into the documents cache live; calls `processDocument(..., true)`. `onSuccess` invalidates invoices, vendors, **vouchers**, and documents.
- `src/routes/inbox.tsx` — dedicated live extraction progress panel driven by `batchItems` state (`queued` → `extracting` → `completed`/`review`/`failed`); overall `done/total` + percent; button label **Upload Files**; no redirect; `displayStatus` joins the linked invoice status so "Needs Review" vs "Extracted" survives a refresh.
- `src/routes/invoices.tsx` — auto-prepare effect: for each listed invoice (`PROCESSED`/`NEEDS_REVIEW`, has a date, no voucher yet) it calls `useCreateVoucherFromInvoice` once, so the **Entry Details** column fills in without opening the row (safety net for invoices not created via the inbox batch). `reviewReasons` message split: dated-but-pending → "Accounting voucher is being prepared"; no date → "Invoice date missing — add it to prepare the accounting voucher".

## How to verify

- Frontend types: `node_modules/.bin/tsc --noEmit -p tsconfig.json` (clean).
- Frontend tests: `node_modules/.bin/vitest run src/test/invoices.test.tsx src/test/documents.test.tsx` (passing).
- Backend tests: from `backend/`, `python -m pytest` → **668 passed, 1 skipped** at handoff.

## Known limitations / next steps

- **"Invoices appear in Invoice Processing only after all 10 complete" is NOT implemented.** The backend creates each invoice as its document finishes, so they appear incrementally. True all-or-nothing gating needs a persisted batch model (a batch id/flag) plus a list filter.
- **`voucher_date` is `NOT NULL`** (`backend/app/models/voucher.py`). An invoice with no extracted date genuinely cannot get a voucher; the UI now prompts to add the date. Removing this constraint would require a DB migration (which rewrites Lovable history — confirm before doing it).
- The inbox progress **cards are session-only**; after a refresh the panel is empty, but each document's status persists (`document.status`) and shows in the table below.
- **Vendor auto-match still runs inside backend extraction** (part of reading the invoice). The inbox UI shows no mapping step, but the match is recorded. Removing it would alter the shared `process_invoice` used by Invoice Processing.
- If extracted invoices still show only amount + vendor, that is DeepSeek prompt/model quality — `_EXTRACTED_FIELDS` already covers all fields; inspect the DeepSeek prompt in `backend/app/services`.

## Repo state at handoff

Nothing was committed this session. Uncommitted working-tree changes:

- This session: `backend/app/api/v1/invoices/router.py`, `backend/app/services/invoice.py`, `backend/tests/test_invoice_extraction.py`, `src/lib/api/invoices.ts`, `src/lib/queries/invoices.ts`, `src/routes/inbox.tsx`, `src/routes/invoices.tsx`.
- Pre-existing (already modified before this session, untouched here): `backend/app/api/v1/documents/router.py`, `backend/app/services/document.py`, `backend/tests/test_documents.py`, `src/components/pal/common.tsx`, `src/components/pal/data-table.tsx`, and untracked `public/tally.png`.

Recent commits: `75db35f first working model`, `b2d3a58 Implement Phase 1 accounting workflow`, `c18ec0c Refactor invoice extraction and connect UI to backend data`.
