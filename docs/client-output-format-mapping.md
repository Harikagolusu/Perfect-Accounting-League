# Client Output Format Mapping

This document maps PAL's canonical data onto the three client workbooks. The
mapping is deterministic and one-way: canonical data is the source of truth, the
workbook is a projection. The exporter never writes generated rows back into
canonical tables.

## 1. Pipeline

```
canonical records
    -> template-specific mapper
    -> validation / exception collection
    -> exact-column row objects
    -> openpyxl workbook based on the client template
    -> preview or .xlsx download
```

AI extraction is upstream of this pipeline. It may fill canonical fields, but it
never generates export rows and never chooses a ledger, tax rate or voucher
balance.

## 2. Canonical additions

Only fields genuinely required by the contracts were added.

| Model | Field | Purpose |
| --- | --- | --- |
| `Company` | `cgst_ledger`, `sgst_ledger`, `igst_ledger`, `cess_ledger` | Ledger names for tax rows in accounting vouchers. |
| `Company` | `round_off_ledger` | Ledger name for a non-zero invoice round-off. |
| `Vendor` | `address`, `pincode` | Party address/pincode in invoice and voucher output. |
| `Vendor` | `msme_registration` | `MSME Reg. No` in the invoice sheet. |
| `Invoice` | `place_of_supply` | Printed GST place of supply. |
| `InvoiceLineItem` | line number, description, supply type, HSN/SAC, quantity, GST rate, taxable/tax/cess/total amounts | Item-level invoice rows. |
| `BankAccount` | `ledger_name` | Bank ledger row in Receipt/Payment vouchers. |
| `BankTransaction` | `counterparty_name`, `counterparty_ledger`, `counterparty_address`, `counterparty_pincode`, `counterparty_gstin` | Human-confirmed counterparty mapping and party row details. |

Existing fields reused without renaming:

- `Vendor.creditor_ledger`
- `Vendor.default_expense_ledger`
- `Invoice.vendor_name`, `vendor_gstin`, `vendor_pan`, `buyer_gstin`
- `Invoice.taxable_amount`, `cgst_amount`, `sgst_amount`, `igst_amount`,
  `cess_amount`, `round_off`, `total_amount`
- `BankTransaction.description`, `debit_amount`, `credit_amount`,
  `transaction_date`, `transaction_type`

No canonical model is named after a spreadsheet column.

## 3. Invoice workbook mapping

Template: `invoice_requirements.xlsx`
Data sheet: `Sheet1`
One output row per invoice line item.

| # | Client column | Canonical source | Required? | Rule |
| --- | --- | --- | --- | --- |
| 1 | `Inv Date` | `Invoice.invoice_date` | Yes | Date cell, `dd/mm/yyyy`. |
| 2 | `Inv Number` | `Invoice.invoice_number` | Yes | Blank -> `INVOICE_FIELD_REQUIRED`. |
| 3 | `Name` | `Invoice.vendor_name`, else `Vendor.name` | Yes | Vendor master must resolve in the same company. |
| 4 | `MSME Reg. No` | `Vendor.msme_registration` | No | Blank -> warning `FIELD_NOT_AVAILABLE`. |
| 5 | `GST Reg No` | `Invoice.vendor_gstin`, else `Vendor.gstin` | Yes | Blank -> `INVOICE_FIELD_REQUIRED`. |
| 6 | `PAN` | `Invoice.vendor_pan`, else `Vendor.pan`, else PAN embedded in a valid 15-character GSTIN | No | Blank -> warning `FIELD_NOT_AVAILABLE`. |
| 7 | `Place of Supply` | `Invoice.place_of_supply` | Yes | Never derived from buyer GSTIN. Blank -> `INVOICE_FIELD_REQUIRED`. |
| 8 | `Address` | `Vendor.address` | No | Blank -> warning `FIELD_NOT_AVAILABLE`. |
| 9 | `Item Discription` | `InvoiceLineItem.description` | Yes | Client spelling preserved. |
| 10 | `Service/Goods` | `InvoiceLineItem.supply_type` | Yes | `GOODS` -> `Goods`; `SERVICE` -> `Service`. |
| 11 | `HSN` | `InvoiceLineItem.hsn_sac` | Yes | Blank -> `INVOICE_FIELD_REQUIRED`. |
| 12 | `Quantity` | `InvoiceLineItem.quantity` | Yes | `NUMERIC(15,3)`, written as a number. |
| 13 | `GST Rate` | `InvoiceLineItem.gst_rate` | Yes | `NUMERIC(5,2)`. Never defaulted. |
| 14 | `Taxable Amount` | `InvoiceLineItem.taxable_amount`; for a single-line invoice, fallback to `Invoice.taxable_amount` | Yes | Fallback only when exactly one line exists. |
| 15 | `IGST` | Line `igst_amount`; single-line fallback to invoice | No | Unknown -> blank. Recorded zero -> `0.00`. |
| 16 | `CGST` | Line `cgst_amount`; single-line fallback to invoice | No | Same rule. |
| 17 | `SGST` | Line `sgst_amount`; single-line fallback to invoice | No | Same rule. |
| 18 | `Invoice Amount` | `InvoiceLineItem.total_amount`; single-line fallback to `Invoice.total_amount` | Yes | Required for each line. |

Multi-line invoices:

- Line amounts are used as recorded.
- If the invoice has tax amounts but a line has no tax allocation, the tax
  columns stay blank and `TAX_ALLOCATION_REQUIRED` is returned as a warning.
  The exporter does not split tax across lines.
- A missing line description, HSN, quantity, GST rate, taxable amount or line
  total excludes that line and is reported as an error.

Single-line fallback exists so a canonical invoice with header amounts and one
line does not require duplicating the same amounts on the line. It never
applies to a multi-line invoice.

## 4. Bank voucher workbook mapping

Template: `bank_upload_reference.xlsx`
Data sheet: `Accounting Voucher`
One voucher per bank transaction, two ledger rows per voucher.

| # | Client column | Canonical source | Rule |
| --- | --- | --- | --- |
| 1 | `Voucher Date` | `BankTransaction.transaction_date` | Written as `DD Mon YYYY` (e.g. `10 Mar 2026`), matching the sample. |
| 2 | `Voucher Type Name` | `transaction_type` | `CREDIT` -> `Receipt`; `DEBIT` -> `Payment`. |
| 3 | `Voucher Number` | Derived | Restarts at 1 per voucher type after deterministic sorting. |
| 4 | `Buyer/Supplier - Address` | `counterparty_address` | On the counterparty row; blank on the bank row. |
| 5 | `Buyer/Supplier - Pincode` | `counterparty_pincode` | On the counterparty row. |
| 6 | `Ledger Name` | Bank row: `BankAccount.ledger_name`; party row: `counterparty_ledger` | Both required. |
| 7 | `Ledger Amount` | Receipt: `credit_amount`; Payment: `debit_amount` | Same amount on both rows. Must be positive. |
| 8 | `Ledger Amount Dr/Cr` | Derived | Receipt: bank `Dr`, party `Cr`. Payment: bank `Cr`, party `Dr`. |
| 9 | `Item Name` | None | Blank; bank vouchers carry no item data. |
| 10 | `Billed Quantity` | None | Blank. |
| 11 | `Item Rate` | None | Blank. |
| 12 | `Item Rate per` | None | Blank. |
| 13 | `Item Amount` | None | Blank. |
| 14 | `Change Mode ` | Constant | `Accounting`. Trailing space preserved. |
| 15 | `Voucher Narration` | `BankTransaction.description` | Written unchanged. |

Validation:

- Missing bank account ledger -> `LEDGER_MAPPING_REQUIRED`.
- Missing counterparty ledger -> `BANK_MAPPING_REQUIRED`.
- Missing/zero bank-side amount -> `BANK_TRANSACTION_AMOUNT_REQUIRED`.
- Unbalanced generated voucher -> `VOUCHER_NOT_BALANCED`.
- A transaction with any error is excluded; valid transactions in the same
  export are still written.

## 5. Accounting voucher workbook mapping

Template: `accounting_vouchers.xlsx`
Data sheet: `Accounting Voucher`
One voucher may occupy many rows.

### 5.1 Purchase invoice voucher

Canonical source: one `Invoice` with a confirmed `Vendor` and company ledger
configuration.

| Client column | Purchase invoice value |
| --- | --- |
| `Voucher Date` | `Invoice.invoice_date`, date cell `dd/mm/yyyy` |
| `Voucher Type Name` | `Purchase` |
| `Voucher Number` | Per-type deterministic sequence |
| `Buyer/Supplier - Address` | `Vendor.address` on the party row |
| `Buyer/Supplier - Pincode` | `Vendor.pincode` on the party row |
| `Ledger Name` | Party: `Vendor.creditor_ledger`; expense: `Vendor.default_expense_ledger`; tax: company tax ledger; round-off: company round-off ledger |
| `Ledger Amount` | Party `Cr` total; expense `Dr` taxable; each tax `Dr`; round-off `Dr` if positive, `Cr` if negative |
| `Ledger Amount Dr/Cr` | As above |
| `Item Name`, quantity, rate, amount | Blank for invoice vouchers |
| `Change Mode ` | `Accounting Invoice` |
| `Ledger Narration` | Blank |
| `Voucher Narration` | Blank |
| `Original Invoice No.` | `Invoice.invoice_number` |
| `Buyer/Supplier - GSTIN/UIN` | `Invoice.vendor_gstin`, else `Vendor.gstin`, on the party row |

Balance rule:

```
party credit total
  = expense debit taxable
  + IGST debit
  + CGST debit
  + SGST debit
  + cess debit
  + round-off debit (or - round-off credit)
```

The mapper checks this and excludes the voucher if it does not balance. It never
adds a plug line.

`Invoice.total_amount` is required. The mapper does not reconstruct a missing
total from taxable + tax + round-off: a missing total would make an incomplete
tax picture look balanced.

Required mappings:

- `Vendor.creditor_ledger`
- `Vendor.default_expense_ledger`
- `Company.cgst_ledger` when CGST > 0
- `Company.sgst_ledger` when SGST > 0
- `Company.igst_ledger` when IGST > 0
- `Company.cess_ledger` when cess > 0
- `Company.round_off_ledger` when round-off is non-zero

### 5.2 Bank vouchers in the accounting workbook

Bank vouchers use the same two-line shape as the bank workbook, with:

- `Voucher Type Name`: `Receipt` or `Payment`
- `Change Mode `: `Accounting`
- `Original Invoice No.`: blank
- `Ledger Narration`: blank
- `Voucher Narration`: bank description
- Party address/pincode/GSTIN from the confirmed counterparty mapping

## 6. Validation and exception catalogue

| Code | Severity | Meaning |
| --- | --- | --- |
| `INVOICE_FIELD_REQUIRED` | Error | A required invoice or line field is missing. |
| `INVOICE_LINE_ITEMS_REQUIRED` | Error | The invoice has no line items. |
| `VENDOR_MAPPING_REQUIRED` | Error | No vendor master resolves in the same company. |
| `TAX_DATA_REQUIRED` | Error | GST rate is missing. Never inferred. |
| `TAX_ALLOCATION_REQUIRED` | Warning | Multi-line invoice has invoice-level tax but no line allocation; tax columns are blank. |
| `FIELD_NOT_AVAILABLE` | Warning | Optional master field (PAN, address, MSME) is blank. |
| `OUTPUT_MAPPING_REQUIRED` | Error | `supply_type` is not `GOODS` or `SERVICE`. |
| `LEDGER_MAPPING_REQUIRED` | Error | A required ledger name is not configured. |
| `BANK_MAPPING_REQUIRED` | Error | A bank transaction has no confirmed counterparty ledger. |
| `BANK_TRANSACTION_AMOUNT_REQUIRED` | Error | The bank-side amount is missing or not positive. |
| `BANK_STATEMENT_REQUIRED` / `BANK_ACCOUNT_REQUIRED` | Error | The transaction's statement/account cannot be resolved. |
| `VOUCHER_NOT_BALANCED` | Error | Debit total does not equal credit total. |
| `VOUCHER_HAS_NO_LEDGER_ROWS` | Error | A voucher has no ledger rows. |
| `LEDGER_AMOUNT_REQUIRED` | Error | A ledger row has no positive amount. |

Errors exclude the affected record. Warnings do not block the row. A preview
returns both lists and the number of distinct excluded records.

## 7. Voucher numbering

- Sort by voucher date, voucher type, source type and source id.
- Number each voucher type independently from 1.
- The number is derived, not persisted, so repeated exports are stable.
- Bank and accounting workbooks use the same rule.

## 8. Company isolation

Every mapper starts with `get_company(db, company_id)` and every query filters on
`company_id`. Vendor and bank lookups are also filtered by the company, so a
record from another company is treated as missing. The API path always includes
`/companies/{company_id}`.

## 9. API endpoints

### Invoices

- `GET /api/v1/companies/{company_id}/invoices/export/preview?invoice_id=...`
- `GET /api/v1/companies/{company_id}/invoices/export.xlsx?invoice_id=...`
- `GET /api/v1/companies/{company_id}/invoices/{invoice_id}/line-items`
- `POST /api/v1/companies/{company_id}/invoices/{invoice_id}/line-items`
- `PATCH /api/v1/companies/{company_id}/invoices/{invoice_id}/line-items/{line_item_id}`
- `DELETE /api/v1/companies/{company_id}/invoices/{invoice_id}/line-items/{line_item_id}`

Omitting `invoice_id` exports every invoice of the company.

### Bank

- `GET /api/v1/companies/{company_id}/bank-transactions/export/preview?statement_id=...&date_from=...&date_to=...`
- `GET /api/v1/companies/{company_id}/bank-transactions/export.xlsx?statement_id=...&date_from=...&date_to=...`
- `PATCH /api/v1/companies/{company_id}/bank-statements/{statement_id}/transactions/{transaction_id}/mapping`

The mapping endpoint changes only counterparty mapping fields. It never changes
amounts, dates, narration or transaction type.

### Accounting vouchers

- `GET /api/v1/companies/{company_id}/accounting-vouchers/export/preview?date_from=...&date_to=...&include_invoices=true&include_bank=true`
- `GET /api/v1/companies/{company_id}/accounting-vouchers/export.xlsx?date_from=...&date_to=...&include_invoices=true&include_bank=true`

## 10. Frontend integration

- `src/lib/api/client.ts` adds `apiDownload`, which keeps binary responses out
  of the JSON wrapper and reads the filename from `Content-Disposition`.
- `src/lib/export.ts` adds `downloadBlob`.
- `src/lib/api/exports.ts` provides typed preview/download calls.
- `src/routes/invoices.tsx` adds **Client Excel** for the selected invoice.
- `src/routes/bank.tsx` adds **Client Excel** for the selected statement and
  current date range.
- `src/routes/tally.tsx` adds **Client Accounting Excel** for the selected
  month/period.

The UI shows the first blocking exception as a toast and does not download a
workbook when no rows can be written. If some rows are valid, it downloads the
partial workbook and reports the excluded exceptions and warnings.

## 11. File naming and idempotency

Generated filenames follow:

```
PAL_<company>_<label>_<YYYYMMDD>.xlsx
```

The exporter is stateless. Re-running the same export over the same records
produces the same rows, the same order and the same voucher numbers. The
workbook's internal metadata may carry a new save timestamp; row content is
deterministic.

## 12. Known data-entry gaps

The export foundation is complete, but the existing forms do not yet capture
every field it needs. Until those forms are extended, the API is the way to set:

- invoice line items and place of supply;
- vendor address, pincode and MSME registration;
- company GST and round-off ledger names;
- bank account ledger name;
- bank transaction counterparty mapping.

The export reports these as exceptions rather than filling them with defaults.
This is deliberate: a downloaded workbook must never contain an invented ledger,
tax rate or balancing line.
