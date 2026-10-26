# Phase 1 Accounting Gap Analysis

## 1. Purpose and scope

This document records the gap between PAL's pre-existing document/extraction
pipeline and the Phase 1 accounting workflow required by the client's
`Accounting_Automation_Flowcharts_and_Process.docx` and the three client
workbooks. It is the evidence behind the implementation in
`docs/phase1-accounting-workflow.md`.

Phase 1 covers:

- a company-scoped Ledger Master;
- explainable ledger suggestions;
- draft vouchers created from invoices or bank transactions;
- human review, editing, approval and rejection;
- an append-only accounting audit trail;
- an approved-voucher import file whose generation is not a posting;
- explicit verification before a voucher becomes `POSTED`.

Phase 1 does **not** cover direct Tally posting, Zoho OAuth, advanced AI
accounting decisions, GST/TDS workings, or GST 2B reconciliation.

## 2. Source of truth

The following client files are treated as contracts and are not modified:

| Source | Use |
| --- | --- |
| `Accounting_Automation_Flowcharts_and_Process.docx` | Required process, statuses and control points. |
| `Invoice Requirements v.00.xlsx` | Invoice input/output headers and terminology. |
| `bank upload reference.xlsx` | Bank-voucher workbook sheet names, headers, styles and Read Me. |
| `AccountingVouchers.xlsx` | Accounting-voucher workbook sheet names, headers, styles and Read Me. |

The workbooks are output contracts, not PAL's internal data model. Bundled
generator copies live under `backend/app/services/exports/templates/`; the
originals in the repository root remain untouched.

## 3. State before Phase 1

### 3.1 What existed

- Text-first document extraction and invoice/bank data capture.
- Company-scoped records with `companyId` and backend company filters.
- Vendor Master fields for default expense ledger and creditor ledger.
- Strict invoice and bank workbook exports with deterministic, idempotent file
  generation.
- Client workbook templates and Read Me sheets.
- Existing modules: Invoice Processing, Bank Statements, Tally Integration.

### 3.2 What was missing

| Gap | Consequence before Phase 1 |
| --- | --- |
| No company-scoped ledger master | A ledger name could be typed into a vendor or company field but was not a first-class, validated company record. |
| No traceable ledger suggestion evidence | There was no machine-readable explanation of why a ledger was proposed. |
| No draft voucher object | Invoices and bank lines could be extracted and exported, but not reviewed as accounting vouchers. |
| No explicit voucher lifecycle | There was no enforced distinction between draft, approved, exported and posted. |
| No approval gate | A file could be generated without a recorded human approval. |
| No append-only accounting audit | Source values, edits, approvals and export/posting decisions were not preserved as accounting history. |
| Export could be mistaken for posting | Generating an import workbook did not have a lifecycle state that explicitly deferred posting. |
| No invoice direction | Purchase and sales were not explicit, so a sales voucher could not be generated safely. |
| No reviewer-confirmed inter-account flag | A bank transfer could be misread from narration. |
| No deterministic balance gate | `TOTAL DEBIT == TOTAL CREDIT` was not enforced before approval. |

## 4. Client requirement to implementation mapping

| Client requirement | Phase 1 implementation | Status |
| --- | --- | --- |
| Document → Extract → Suggest → Draft Voucher → Review → Approval → Posting/Export → Verification → Audit Trail | Voucher source links, explainable suggestions, draft factory, review UI, state machine, approved export and verified posting | Implemented |
| Never Document → AI → auto-post | Draft creation always starts at `DRAFT` or `NEEDS_REVIEW`; no endpoint creates `POSTED` directly | Implemented |
| AI may extract/verify and suggest ledgers only | Suggestions use configured or historical evidence; voucher amounts and lines are deterministic | Implemented |
| Never invent ledger mappings, rates or tax treatment | Missing evidence returns machine-readable exception codes and routes to review | Implemented |
| `TOTAL DEBIT == TOTAL CREDIT` before approval | Deterministic validation blocks approval/export of unbalanced vouchers | Implemented |
| Exact status vocabulary | `DRAFT`, `PENDING_APPROVAL`, `REJECTED`, `APPROVED`, `POSTING_IN_PROGRESS`, `POSTED`, `POSTING_FAILED`, `AWAITING_IMPORT_CONFIRMATION`, `NEEDS_REVIEW` | Implemented |
| Disallow direct `DRAFT → POSTED` | Transition table rejects it; posting requires approval and verification | Implemented |
| Company-scoped ledger CRUD | `/companies/{company_id}/ledgers`; foreign ids return 404 | Implemented |
| A Company A ledger is unusable by Company B | Every lookup and voucher line validation is company-scoped | Implemented |
| Suggestions traceable to history/rules | Suggestion response includes purpose, source, reason and exception code | Implemented |
| Approval records who/when/status/action/comment | Approval fields plus append-only audit event | Implemented |
| Audit preserves source, edits, ledger changes, approvals, exports and posting references | `accounting_audit_events`, one event per material action | Implemented |
| Export must not mark `POSTED` | Approved export moves to `AWAITING_IMPORT_CONFIRMATION`; `POSTED` only after verified external reference | Implemented |
| Preserve workbook contracts and company isolation | Existing templates and generators reused; new export is additive | Implemented |
| No new sidebar modules | Workflow is inside Invoice Processing, Bank Statements and Tally Integration | Implemented |
| No later-phase scope | No direct Tally posting, Zoho OAuth, GST/TDS workings or 2B reconciliation | Respected |

## 5. What was added

### 5.1 Database

- `ledgers` with company ownership, optional free-text type/group/tax
  classification, source provenance and active flag.
- `ledger_sources` for provenance links.
- `accounting_mappings` for configured, traceable suggestion rules.
- `vouchers` and `voucher_lines` with controlled voucher types, deterministic
  numbers, source links and lifecycle fields.
- `accounting_audit_events` as the append-only decision history.
- Canonical fields: `companies.sales_ledger`, `invoices.direction`,
  `bank_transactions.is_inter_account_transfer` and counterparty mapping fields.
- Migration `0011_add_phase1_accounting.py`, applied and checked with
  `alembic upgrade head` and `alembic check`.

### 5.2 Backend services

- `ledger.py`: company-scoped CRUD, name resolution and deactivation.
- `accounting_mapping.py`: configured suggestion rules.
- `ledger_suggestion.py`: explainable invoice and bank suggestions.
- `voucher_validation.py`: deterministic line, ledger and balance checks.
- `voucher.py`: lifecycle transitions, approval, rejection, reopen, export and
  posting verification.
- `voucher_factory.py`: idempotent draft creation from invoices and bank lines.
- `voucher_export.py`: approved-voucher workbook generation.
- `accounting_audit.py`: append-only event recording and listing.

### 5.3 API

All accounting routes are nested under
`/api/v1/companies/{company_id}`:

- `/ledgers` and `/ledgers/{ledger_id}`
- `/accounting-mappings` and `/accounting-mappings/{mapping_id}`
- `/vouchers`, `/vouchers/{voucher_id}`, `/vouchers/from-invoice/{id}`,
  `/vouchers/from-bank/{id}`
- `/vouchers/{voucher_id}/submit|approve|reject|reopen|export|verify-posting|audit`
- `/vouchers/export` and `/vouchers/export/preview`
- `/accounting-audit`
- `/invoices/{invoice_id}/ledger-suggestion`
- `/bank-statements/{statement_id}/transactions/{transaction_id}/ledger-suggestion`

### 5.4 Frontend

- Ledger Master and company ledger defaults in Tally Integration.
- Approved-voucher export in Tally Integration.
- Accounting Voucher panel in Invoice Processing.
- Accounting Voucher and counterparty mapping panel in Bank Statements.
- Shared `VoucherReview` component for line editing, exceptions, approval,
  rejection, export and posting verification.

No new sidebar item was added.

## 6. Remaining gaps and explicit limitations

1. **No authentication or RBAC exists in the repository.** The actor is a
   caller-supplied audit label (`X-PAL-Actor` or request body), defaulting to
   `local-user`. This records responsibility but does not prove identity or
   enforce segregation of duties. A real auth integration is required before
   production approval controls are meaningful.
2. **No direct Tally or Zoho posting.** `POSTED` is recorded only after a human
   supplies an external posting reference. There is no live accounting-software
   connector.
3. **No fixed chart of accounts taxonomy.** Ledger `ledger_type`,
   `parent_group` and `tax_classification` are free text because the client has
   not supplied a fixed list.
4. **Sales vouchers use the existing party fields for the counterparty** and a
   new optional company `sales_ledger`. A dedicated customer master is later
   scope.
5. **Bank inter-account transfers are reviewer-flagged.** PAL never infers them
   from narration.
6. **No GST/TDS computation or 2B reconciliation.** Tax amounts are carried as
   extracted or entered facts; the system does not calculate statutory
   treatment.
7. **No automatic balancing line or rounding repair.** Unbalanced vouchers stay
   unbalanced and are routed to review.
8. **Import confirmation is manual.** The system cannot prove a workbook was
   imported; a human must verify the external voucher and record the reference.
9. **Workbook contract fidelity is limited to the supplied templates.** New
   client-required columns or changed headers require a template update.
10. **Invoice items are aggregated onto one main voucher line.** Phase 1 does
    not map a different ledger per item. Item fields are copied only when the
    invoice has exactly one item, so a multi-item invoice never shows one
    item's HSN/quantity against the whole aggregate amount.
11. **Audit immutability is application-level.** The API never updates or
    deletes `accounting_audit_events`, but the database does not yet enforce
    append-only writes with a trigger. The existing GST 2B reconciliation screen
    remains local-only and is out of Phase 1 scope, so it does not write an
    accounting reconciliation decision.

## 7. Information still required from the client

- The authoritative chart of accounts, including allowed ledger types, parent
  groups and tax classifications.
- Whether sales require a separate customer master or may reuse vendor/party
  fields.
- Approval roles and segregation-of-duties rules, plus the identity provider to
  integrate.
- The exact posting reference format returned by Tally Prime and Zoho Books.
- Whether bank inter-account transfers need a paired-bank-account selection
  rather than a single flag.
- Final GST/TDS treatment rules, including ITC eligibility, for later phases.
- Confirmation of voucher-number series and financial-year reset behaviour.
- Whether the client wants a manual "mark posting failed" reason taxonomy in
  addition to the current free-text note.
- Whether multi-item invoices need per-item ledger mapping in the accounting
  voucher, and if so, the client's rule for splitting tax and discount.

## 8. Verification record

At the time of this document:

- PostgreSQL 16 connectivity: verified against `pal_db`.
- Phase 1 backend tests: `33 passed`.
- Full backend suite: `521 passed`.
- Frontend tests: `157 passed` across 11 files.
- TypeScript: `tsc --noEmit` clean.
- Production build: passed.
- ESLint on changed frontend files: clean.
- Migration: `alembic current` at `0011_add_phase1_accounting`; `alembic upgrade
  head` applied; `alembic check` reports no new upgrade operations.
- Rejection hardening: a blank reason now returns
  `REJECTION_REASON_REQUIRED`; the voucher stays in its previous status.
