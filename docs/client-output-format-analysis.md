# Client Output Format Analysis

This document records what the three client workbooks actually contain. It is the
evidence behind the mappings in `docs/client-output-format-mapping.md`.

The workbooks are treated as **output contracts**, not as PAL's data model. They
are reference templates: generated files copy their sheet names, headers, header
styling, column widths and Read Me sheets, then replace only the sample data rows
on the data sheet.

## 1. Source workbooks

| File | Data sheet | Data sheet dimensions | Data rows | Additional sheet |
| --- | --- | --- | --- | --- |
| `Invoice Requirements v.00.xlsx` | `Sheet1` | 1 × 18 | 0 (header only) | none |
| `bank upload reference.xlsx` | `Accounting Voucher` | 687 × 15 | 686 (343 two-line vouchers) | `Accounting Voucher (Read Me)` |
| `AccountingVouchers.xlsx` | `Accounting Voucher` | 55 × 18 | 54 (18 three-line vouchers) | `Accounting Voucher (Read Me)` |

The original files remain untouched in the repository root. Bundled copies used
by the generator live in
`backend/app/services/exports/templates/`.

## 2. Invoice Requirements v.00.xlsx

### Sheet

- Name: `Sheet1`
- Header row: row 1 only
- No sample data rows
- No Read Me sheet

### Exact header order

1. `Inv Date`
2. `Inv Number`
3. `Name`
4. `MSME Reg. No`
5. `GST Reg No`
6. `PAN`
7. `Place of Supply`
8. `Address`
9. `Item Discription`
10. `Service/Goods`
11. `HSN`
12. `Quantity`
13. `GST Rate`
14. `Taxable Amount`
15. `IGST`
16. `CGST`
17. `SGST`
18. `Invoice Amount`

### Observations

- `Item Discription` is the client's spelling. It is preserved verbatim in the
  generated workbook. Correcting it would break a header-based import.
- The header-only template does not say whether an invoice may occupy more than
  one row. The presence of item-level columns (`Item Discription`, `HSN`,
  `Quantity`, `GST Rate`) means the safe interpretation is **one row per invoice
  line item**, with the invoice header repeated on each row.
- `Service/Goods` is a label, not a code. The canonical values `GOODS` and
  `SERVICE` are written as `Goods` and `Service`.
- `MSME Reg. No`, `PAN` and `Address` are supplier master fields. They are not
  invoice calculations.
- `Place of Supply` is a printed invoice fact. It cannot be inferred from the
  buyer's GSTIN without guessing.
- `GST Rate` is an input, never a derived value. A missing rate is an exception,
  not a default such as 18%.
- `IGST`, `CGST` and `SGST` are tax amounts. A recorded zero may be written as
  `0.00`; an unknown tax amount is left blank and reported.

## 3. bank upload reference.xlsx

### Sheet

- Data sheet: `Accounting Voucher`
- Header row: row 1
- Sample rows: 686
- Voucher structure: 343 vouchers, each with exactly two ledger rows
- Voucher types: 198 `Payment`, 145 `Receipt`
- `Change Mode ` value: `Accounting`

### Exact header order

1. `Voucher Date`
2. `Voucher Type Name`
3. `Voucher Number`
4. `Buyer/Supplier - Address`
5. `Buyer/Supplier - Pincode`
6. `Ledger Name`
7. `Ledger Amount`
8. `Ledger Amount Dr/Cr`
9. `Item Name`
10. `Billed Quantity`
11. `Item Rate`
12. `Item Rate per`
13. `Item Amount`
14. `Change Mode ` — **trailing space is part of the header**
15. `Voucher Narration`

### Observations

- Dates are strings such as `04 Apr 2025`, not Excel date cells.
- `Voucher Number` restarts per voucher type in the sample. It is not a global
  sequence.
- Every sample voucher has exactly two rows: the bank ledger and the
  counterparty ledger.
- Receipt direction: bank ledger `Dr`, counterparty `Cr`.
- Payment direction: bank ledger `Cr`, counterparty `Dr`.
- `Item Name`, `Billed Quantity`, `Item Rate`, `Item Rate per` and `Item Amount`
  are blank throughout the bank sample. The columns remain in the contract.
- `Buyer/Supplier - Address` and `Buyer/Supplier - Pincode` belong on the
  counterparty row, not the bank row.
- The bank sample carries no GSTIN column. The accounting voucher template does.
- `Voucher Narration` carries the bank narration unchanged.

### Read Me sheet

`Accounting Voucher (Read Me)` is a TallyPrime field list and import guide. It is
not a PAL business-rules document. It is preserved because the client expects it
in the delivered workbook.

## 4. AccountingVouchers.xlsx

### Sheet

- Data sheet: `Accounting Voucher`
- Header row: row 1
- Sample rows: 54
- Sample structure: 18 `sales` vouchers, each with three ledger rows
- Sample row pattern: party `Dr`, sales `Cr`, tax `Cr`
- `Change Mode ` value: `Accounting Invoice`

### Exact header order

1. `Voucher Date`
2. `Voucher Type Name`
3. `Voucher Number`
4. `Buyer/Supplier - Address`
5. `Buyer/Supplier - Pincode`
6. `Ledger Name`
7. `Ledger Amount`
8. `Ledger Amount Dr/Cr`
9. `Item Name`
10. `Billed Quantity`
11. `Item Rate`
12. `Item Rate per`
13. `Item Amount`
14. `Change Mode ` — **trailing space is part of the header**
15. `Ledger Narration`
16. `Voucher Narration`
17. `Original Invoice No.`
18. `Buyer/Supplier - GSTIN/UIN`

### Observations

- This is the bank contract plus three columns (`Ledger Narration`,
  `Voucher Narration`, `Original Invoice No.`) and a GSTIN column.
- The sample is a sales voucher set, but the row shape is generic: one voucher
  may contain any number of ledger rows. PAL's canonical purchase invoice maps
  to the same shape with a `Purchase` voucher type.
- Sample dates are real date values formatted with `d/mm-dd-yy`. The generator
  writes real date cells with `dd/mm/yyyy`, which is unambiguous and matches the
  date values' intent. The exact sample format is not copied because its token
  sequence is internally inconsistent.
- `Item Name` and the quantity/rate/amount columns are blank in the sample. They
  remain available in the contract.
- `Ledger Narration` and `Voucher Narration` are blank in the sample.
- `Original Invoice No.` links a purchase/sales voucher to its source invoice.
- `Buyer/Supplier - GSTIN/UIN` belongs on the party ledger row.
- A voucher must balance: total debit equals total credit. The sample satisfies
  this, and generated vouchers are checked before they are written.

### Read Me sheet

`Accounting Voucher (Read Me)` is again a TallyPrime field list and import guide.
It contains merged explanation ranges (`E5:Q9`, `E19:Q22`) and is preserved
unchanged.

## 5. Shared output concepts

The two voucher workbooks share one row shape:

- A voucher is a header (`Voucher Date`, `Voucher Type Name`, `Voucher Number`,
  `Change Mode `, narrations) plus one or more ledger rows.
- `Ledger Amount Dr/Cr` is the direction of the row, not a sign on the amount.
- Voucher numbers are per voucher type.
- The party address, pincode and GSTIN belong on the party ledger row.
- A voucher is valid only when debit and credit totals match.

## 6. Decisions forced by the analysis

| Question | Decision |
| --- | --- |
| Is an invoice one row or many? | One row per canonical line item. The invoice header is repeated. |
| What if an invoice has no line items? | Exception `INVOICE_LINE_ITEMS_REQUIRED`; no row is written. |
| Can `Place of Supply` be derived? | No. It is required and must be recorded. |
| Can `GST Rate` be defaulted? | No. Missing rate is `TAX_DATA_REQUIRED`. |
| Can tax be apportioned across lines? | No. Multi-line tax allocation must be explicit; otherwise a warning and blank tax columns. |
| Can a bank counterparty ledger be guessed from narration? | No. It must be confirmed on the transaction. |
| Can a missing ledger become a default name? | No. `LEDGER_MAPPING_REQUIRED`. |
| Can an unbalanced voucher be forced to balance? | No. `VOUCHER_NOT_BALANCED`; the voucher is excluded. |
| Can an invoice be exported without a vendor master? | No. `VENDOR_MAPPING_REQUIRED`, even if a printed supplier name exists. |
| Are client typos corrected? | No. `Item Discription` and the trailing space in `Change Mode ` are preserved. |
| Is the Read Me sheet regenerated? | No. It is copied from the client template. |
| Are original workbooks overwritten? | No. Generated files are separate downloads. |

## 7. Determinism and isolation

- The export layer is read-only. It does not persist vouchers or rows.
- The same canonical records produce the same rows in the same order.
- Invoice rows are ordered by invoice date, invoice number, creation time and id.
- Bank vouchers are ordered by transaction date, creation time and id.
- Voucher numbers are derived per voucher type from that order.
- Every query filters on `company_id`. A record belonging to another company is
  treated as not found.
- Empty datasets still produce a workbook with the client's header and Read Me
  sheet; they do not produce fake sample rows.

## 8. Out of scope

- Rewriting extraction or OCR.
- A bulk upload/processing queue.
- Persisting generated vouchers.
- Automatically deciding tax rates, HSN codes, places of supply or ledger names.
- Replacing the client templates with a PAL-designed workbook.
