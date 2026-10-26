# Phase 1 Accounting Workflow

## 1. End-to-end flow

```
Document
  -> Extract
  -> Suggest ledgers (explainable, evidence only)
  -> Draft voucher (deterministic, idempotent)
  -> Human review / edit
  -> Submit for approval
  -> Approve or reject
  -> Approved import file
  -> Awaiting import confirmation
  -> Verify external posting
  -> Posted
  -> Append-only audit trail
```

The system never performs `Document -> AI -> auto-post`. Draft creation always
starts at `DRAFT` or `NEEDS_REVIEW`. `POSTED` is reachable only from
`AWAITING_IMPORT_CONFIRMATION` or `POSTING_IN_PROGRESS`, and only with an
external posting reference.

## 2. Status model

| Status | Meaning | Who or what sets it |
| --- | --- | --- |
| `DRAFT` | Editable voucher that has not been submitted. | Draft factory or reviewer. |
| `PENDING_APPROVAL` | Submitted and waiting for a human decision. | `submit`. |
| `REJECTED` | Returned for correction with a recorded reason. | `reject`. |
| `APPROVED` | Human-approved and eligible for export. | `approve`. |
| `POSTING_IN_PROGRESS` | External posting is in progress. | Reserved transition for a connector; not used by the Phase 1 UI. |
| `POSTED` | Verified as posted in the external system. | `verify-posting` with `verified=true` and a reference. |
| `POSTING_FAILED` | Posting was attempted and failed. | `verify-posting` with `verified=false`. |
| `AWAITING_IMPORT_CONFIRMATION` | Import file generated; not yet confirmed as posted. | Approved-voucher export. |
| `NEEDS_REVIEW` | Deterministic validation or missing evidence requires a human. | Draft creation, edit, submit or approval checks. |

### Allowed transitions

| From | To |
| --- | --- |
| `DRAFT` | `PENDING_APPROVAL`, `NEEDS_REVIEW`, `REJECTED` |
| `NEEDS_REVIEW` | `DRAFT`, `PENDING_APPROVAL`, `REJECTED` |
| `PENDING_APPROVAL` | `APPROVED`, `REJECTED`, `NEEDS_REVIEW` |
| `REJECTED` | `DRAFT`, `NEEDS_REVIEW` |
| `APPROVED` | `POSTING_IN_PROGRESS`, `AWAITING_IMPORT_CONFIRMATION`, `NEEDS_REVIEW` |
| `AWAITING_IMPORT_CONFIRMATION` | `POSTED`, `POSTING_FAILED`, `NEEDS_REVIEW` |
| `POSTING_IN_PROGRESS` | `POSTED`, `POSTING_FAILED`, `NEEDS_REVIEW` |
| `POSTING_FAILED` | `POSTING_IN_PROGRESS`, `NEEDS_REVIEW`, `DRAFT` |
| `POSTED` | Terminal |

`DRAFT -> POSTED` is not an allowed transition.

## 3. Ledger Master

The Ledger Master is company-scoped. Every request carries the owning
`company_id` in the URL, and every service lookup includes it. A ledger id from
another company returns `404` and can never be attached to a voucher.

Each ledger stores:

- required name;
- optional code;
- optional free-text `ledger_type`, `parent_group` and `tax_classification`;
- source provenance (`MANUAL`, `IMPORT`, `TALLY`, `ZOHO`);
- optional external Tally/Zoho ids;
- active flag.

Deactivation does not delete history. Voucher lines keep the ledger-name
snapshot, and validation reports `LEDGER_INACTIVE` if an inactive ledger is used
on a new approval attempt.

### Company ledger defaults

Tally Integration exposes the company-level ledger names used by deterministic
voucher generation:

- CGST, SGST, IGST and cess ledgers;
- round-off ledger;
- sales ledger.

These are names, not invented records. If a configured name is not present in
the company's Ledger Master, the suggestion response carries `MISSING_LEDGER`
and the voucher is routed to review.

## 4. Source paths

### 4.1 Invoice

1. Extraction or manual entry stores invoice facts, including an explicit
   `direction` (`PURCHASE` or `SALES`).
2. Invoice Processing shows explainable ledger suggestions.
3. `POST /companies/{company_id}/vouchers/from-invoice/{invoice_id}` creates or
   returns the single draft for that invoice.
4. Purchase drafts use the vendor expense/creditor evidence and company tax
   ledgers. Sales drafts use the company sales ledger and the invoice party.
5. The reviewer edits lines, submits, approves and exports.

Invoice drafts are idempotent by `(company_id, source_type, source_id)`.

### 4.2 Bank transaction

1. Bank Statements stores the bank facts exactly as entered.
2. A reviewer confirms the counterparty name and ledger and may flag an
   inter-account transfer. PAL never infers this from narration.
3. `POST /companies/{company_id}/vouchers/from-bank/{transaction_id}` creates or
   returns the single draft:
   - credit on the bank statement becomes a `RECEIPT`;
   - debit becomes a `PAYMENT`;
   - a reviewer-confirmed inter-account transfer becomes `CONTRA`;
   - split lines may be supplied when the reviewer is splitting one bank line.
4. Missing counterparty or missing bank-account ledger becomes a review
   exception.

### 4.3 Manual

A manual draft can be created through `POST /companies/{company_id}/vouchers`
with a controlled voucher type (`PURCHASE`, `SALES`, `RECEIPT`, `PAYMENT`,
`CONTRA`, `JOURNAL`) and explicit lines.

## 5. Ledger suggestion rules

Suggestions are evidence-only. A suggestion may come from:

| Source | Meaning |
| --- | --- |
| `APPROVED_MAPPING` | An active configured accounting mapping matched the vendor, counterparty or narration. |
| `HISTORICAL_VOUCHER` | A prior approved/awaiting/posted voucher for the same vendor used exactly one candidate ledger. |
| `CONFIGURED_VENDOR_LEDGER` | Vendor Master default expense or creditor ledger. |
| `CONFIGURED_COMPANY_LEDGER` | Company tax, round-off or sales ledger. |
| `CONFIRMED_COUNTERPARTY_LEDGER` | Reviewer-confirmed bank counterparty ledger. |
| `CONFIGURED_BANK_ACCOUNT_LEDGER` | The bank account's configured ledger. |

If there is no evidence, the response includes `NO_HISTORICAL_MAPPING` and the
draft is routed to review. If evidence points at more than one ledger, the
response includes `AMBIGUOUS_ACCOUNTING_MAPPING`.

Every suggestion carries `purpose`, `ledger_id`/`ledger_name`, `source`,
`reason` and an optional `code`, so the UI can explain the proposal.

## 6. Deterministic validation

Validation never repairs a voucher. It returns all issues and routes the voucher
to `NEEDS_REVIEW` when any error is present. No amount is changed and no
balancing line is inserted.

| Code | Meaning |
| --- | --- |
| `VOUCHER_DATE_REQUIRED` | Voucher date is missing. |
| `VOUCHER_TYPE_REQUIRED` | Voucher type is missing. |
| `VOUCHER_HAS_NO_LEDGER_ROWS` | No lines exist. |
| `DUPLICATE_LINE_NUMBER` | A line number appears more than once. |
| `LEDGER_AMOUNT_REQUIRED` | A line amount is missing or not positive. |
| `MISSING_LEDGER` | A line has no company ledger master link. |
| `LEDGER_INACTIVE` | The linked ledger is inactive. |
| `VOUCHER_NOT_BALANCED` | Total debit does not equal total credit. |
| `APPROVAL_REQUIRED` | Approval was attempted without `PENDING_APPROVAL`. |
| `NO_HISTORICAL_MAPPING` | No traceable ledger evidence exists. |
| `AMBIGUOUS_ACCOUNTING_MAPPING` | Evidence points at multiple ledgers. |
| `MISSING_TAX_DATA` | Invoice tax facts are incomplete for a deterministic line. |
| `MISSING_COUNTERPARTY` | Bank transaction has no confirmed counterparty. |
| `MISSING_AMOUNT` / `MISSING_TOTAL` | Required extracted amount is missing. |
| `MISSING_VOUCHER_DATE` | Source date cannot produce a voucher date. |
| `REJECTION_REASON_REQUIRED` | Rejection was attempted without a non-blank reason. |
| `POSTING_NOT_VERIFIED` | Posting verification lacks an external reference. |

Approval performs the same validation again. If issues exist, the voucher is
moved to `NEEDS_REVIEW`, an approval-blocked audit event is written, and the API
returns `409` with the issue list.

## 7. Approval, rejection and reopening

- Approval is allowed only from `PENDING_APPROVAL` and only when validation has
  no errors.
- Approval records `approved_by`, `approved_at`, previous status, new status,
  action and optional comment in the audit trail.
- Rejection is allowed from `PENDING_APPROVAL` or `NEEDS_REVIEW`; a non-blank
  reason is required by both the API and the UI and stored on the voucher and
  audit event. A rejection without one returns `REJECTION_REASON_REQUIRED`.
- Reopening is allowed from `REJECTED`, `POSTING_FAILED` or `NEEDS_REVIEW` and
  returns the voucher to `DRAFT`.
- `POSTED` is terminal.

Actor handling: the repository has no authentication or RBAC. The API reads
`X-PAL-Actor` or an explicit body actor, defaulting to `local-user`. This is an
audit label, not a verified identity. A real identity provider and role model
are required before production segregation of duties.

## 8. Export and posting verification

### Approved export

`POST /companies/{company_id}/vouchers/export` and
`POST /companies/{company_id}/vouchers/{voucher_id}/export`:

1. accept only `APPROVED` or `AWAITING_IMPORT_CONFIRMATION` vouchers;
2. generate the client-format workbook using the bundled templates;
3. move `APPROVED` to `AWAITING_IMPORT_CONFIRMATION`;
4. set `exported_at`, `exported_by` and increment `export_count`;
5. write a `VOUCHER_EXPORTED` audit event with `posted: false`.

Generating the file does **not** set `POSTED`.

### Verified posting

`POST /companies/{company_id}/vouchers/{voucher_id}/verify-posting` accepts:

```json
{
  "actor": "reviewer",
  "verified": true,
  "external_reference": "Tally voucher id",
  "reason": "Checked in Tally"
}
```

- `verified=true` requires a non-empty external reference and moves the voucher
  to `POSTED`.
- `verified=false` moves it to `POSTING_FAILED` and records the reason.
- Both write an audit event with the external reference and previous/new status.

## 9. Audit trail

`accounting_audit_events` is append-only. It records:

- voucher creation and source type;
- source id/document id;
- extracted values and the validation issue list at creation;
- user edits (field, old value, new value);
- ledger changes, including company ledger default changes;
- reviewer-confirmed bank counterparty ledger and inter-account flag changes;
- submit, approve, reject, reopen and status changes;
- export events with filename, export count and `posted: false`;
- posting verification with the external reference;
- reviewer comments on approval, rejection and posting verification.

Endpoints:

- `GET /companies/{company_id}/accounting-audit`
- `GET /companies/{company_id}/vouchers/{voucher_id}/audit`

The UI shows the latest events in `VoucherReview`.

## 10. Frontend locations

| Location | Purpose |
| --- | --- |
| `src/routes/invoices.tsx` | Invoice direction, Accounting Voucher panel, suggestion and review. |
| `src/routes/bank.tsx` | Counterparty mapping, inter-account flag, Accounting Voucher panel. |
| `src/routes/tally.tsx` | Ledger Master, company ledger defaults, approved-voucher export. |
| `src/components/pal/voucher-review.tsx` | Shared review/edit/approval/export/posting UI. |
| `src/components/pal/ledger-master.tsx` | Ledger CRUD and company defaults. |
| `src/components/pal/approved-voucher-export.tsx` | Approved-voucher workbook generation. |
| `src/lib/api/ledgers.ts` | Ledger API contract. |
| `src/lib/api/vouchers.ts` | Voucher, suggestion, export and audit API contract. |
| `src/lib/queries/ledgers.ts` | Ledger query/mutation bindings. |
| `src/lib/queries/vouchers.ts` | Voucher query/mutation bindings. |

No new sidebar item is introduced. The workflow lives inside the existing
Invoice Processing, Bank Statements and Tally Integration modules.

## 11. Operating procedure

1. In Tally Integration, add the company's ledgers and save the company ledger
   defaults.
2. In Invoice Processing, set the invoice direction and confirm the vendor
   mapping.
3. Review the explainable ledger suggestions. If the response is
   `NO_HISTORICAL_MAPPING`, select the correct company ledger in the draft.
4. Create the draft voucher.
5. Correct any lines, amounts, ledgers or item fields (item, HSN/SAC, quantity,
   rate, item amount, tax type). Save the draft.
6. Submit for approval.
7. Approve, or reject with a reason and reopen for correction.
8. In Tally Integration, generate the approved-voucher workbook.
9. Import the workbook into Tally/Zoho outside PAL.
10. Check the imported voucher, then record the external reference and mark it
    posted.
11. Use the audit trail to show who changed what, when, and why.
