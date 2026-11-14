"""Build the GST working workbook in the client's eleven-sheet layout.

The layout mirrors the accountant's reference file (GST <MONTH> <YEAR>). Sheets
PAL can compute from Tally vouchers — Sales, Purchase, the Abstract totals,
Invoice Details — are filled; the sheets that need data PAL does not hold yet
(Deferred, Reverse charge, E-Inv, E-invoice reco, HSN, GST charged off, Reclaim)
are written as empty, labelled templates for the accountant to complete.

Read-only: it consumes an already-computed detail result and returns .xlsx bytes.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from app.services.tally_sync import TallyGstDetailResult, TallyGstLine

_MONEY_FORMAT = "#,##0.00"

# Fonts matching the client workbook: Times New Roman throughout, except the
# Sales data body, which the reference keeps in Calibri.
_TNR = "Times New Roman"
_HEADER_FONT = Font(name=_TNR, size=11, bold=True)
_TOTAL_FONT = Font(name=_TNR, size=11, bold=True)
_CALIBRI = Font(name="Calibri", size=11)
_TNR11 = Font(name=_TNR, size=11)
_ABS_FONT = Font(name=_TNR, size=10)
_ABS_BOLD = Font(name=_TNR, size=10, bold=True)
_BOLD = _HEADER_FONT  # back-compat for existing header helper

# Fills: literal RGBs and the reference theme colors resolved to RGB
# (standard Office theme — grey = lt1 tint -0.35, light-orange = accent2 tint
# 0.8, green total = accent6).
_YELLOW = PatternFill("solid", fgColor="FFFFFF00")
_GREEN = PatternFill("solid", fgColor="FF92D050")
_ACCENT6 = PatternFill("solid", fgColor="FF70AD47")
_GREY = PatternFill("solid", fgColor="FFA6A6A6")
_LIGHT_ORANGE = PatternFill("solid", fgColor="FFFBE5D6")


def _finalize(ws: Worksheet) -> None:
    """Thousands-separator every number and size columns to their widest cell."""
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            is_formula = isinstance(cell.value, str) and cell.value.startswith("=")
            if isinstance(cell.value, (int, float)) or is_formula:
                cell.number_format = _MONEY_FORMAT
            if cell.value is not None:
                col = cell.column
                widths[col] = max(widths.get(col, 10), min(len(str(cell.value)) + 2, 48))
    for col, width in widths.items():
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = width


def _style_row(ws: Worksheet, row: int, last_col: int, font: Font, fill: PatternFill | None = None) -> None:
    """Apply a font (and optional fill) across one row's used columns."""
    for col in range(1, last_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = font
        if fill is not None:
            cell.fill = fill
_MONTHS = [
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
]


def _f(value: Decimal | None) -> float:
    """openpyxl cannot write Decimal; convert for the cell (display export)."""
    return float(value or 0)


def _rate(line: TallyGstLine) -> float:
    """Blended GST rate as a fraction (0.18 = 18%), 0 when no taxable base."""
    if line.taxable:
        return round(float(line.total_gst) / float(line.taxable), 4)
    return 0.0


def _tag(line: TallyGstLine) -> str:
    """A zero-tax taxable supply is treated as an export; otherwise B2B."""
    if line.taxable and not line.total_gst:
        return "Export"
    return "B2B"


def _header(ws: Worksheet, row: int, values: list) -> None:
    """Write a header row in the client's yellow + Times New Roman bold style."""
    for col, value in enumerate(values, 1):
        cell = ws.cell(row=row, column=col, value=value)
        cell.font = _HEADER_FONT
        cell.fill = _YELLOW


def _build_sales(ws: Worksheet, lines: list[TallyGstLine]) -> int:
    """Fill the Sales sheet; return the totals-row index for the Abstract links.

    Total gst, Invoice Value and Tax Rate are Excel formulas (client request), so
    an edit to a tax cell re-totals live, exactly as in the reference workbook.
    """
    _header(ws, 1, [
        "Invoice No.", "Customer Name", "GSTN", "Invoice Date", "Taxable value",
        "IGST", "Central Tax", "State Tax", "Total gst", "Invoice Value",
        "Tax Rate(%)", "", "Voucher No", "Voucher Type",
    ])
    first = 2
    r = first
    for line in lines:
        ws.cell(row=r, column=1, value=line.voucher_number or "")
        ws.cell(row=r, column=2, value=line.party_name or "")
        ws.cell(row=r, column=3, value=line.gstin or "")  # party GSTIN from ledger master
        ws.cell(row=r, column=4, value=line.date.isoformat() if line.date else "")
        ws.cell(row=r, column=5, value=_f(line.taxable))
        ws.cell(row=r, column=6, value=_f(line.igst))
        ws.cell(row=r, column=7, value=_f(line.cgst))
        ws.cell(row=r, column=8, value=_f(line.sgst))
        ws.cell(row=r, column=9, value=f"=F{r}+G{r}+H{r}")       # Total gst
        ws.cell(row=r, column=10, value=f"=I{r}+E{r}")            # Invoice Value
        ws.cell(row=r, column=11, value=f"=I{r}/E{r}")            # Tax Rate(%)
        ws.cell(row=r, column=12, value=_tag(line))
        ws.cell(row=r, column=13, value=line.voucher_number or "")
        ws.cell(row=r, column=14, value=line.voucher_type or "")
        _style_row(ws, r, 14, _CALIBRI)  # reference keeps Sales data in Calibri
        r += 1
    last = r - 1
    r += 1  # blank spacer, as in the reference
    for col in range(5, 11):  # SUM taxable..invoice value
        letter = ws.cell(row=1, column=col).column_letter
        cell = ws.cell(row=r, column=col, value=f"=SUM({letter}{first}:{letter}{last})" if last >= first else 0)
        cell.font = _TOTAL_FONT
        cell.fill = _ACCENT6
    return r


def _build_purchase(ws: Worksheet, lines: list[TallyGstLine], period_month: str) -> int:
    """Fill the Purchase sheet; return the totals-row index for the Abstract links.

    transaction_type / entity_number carry the Tally voucher type / number
    (client: GSTN and bill numbers are not in Tally). Total GST, Invoice Value and
    GST Rate are Excel formulas.
    """
    _header(ws, 1, [
        "Month", "date", "account_name", "entity_number", "GSTN",
        "transaction_id", "transaction_type", "Taxable Value", "IGST", "CGST",
        "SGST", "Total GST", "Invoice Value", "GST Rate",
        "Voucher Type", "Voucher No", "Invoice No.",
    ])
    first = 2
    r = first
    for line in lines:
        ws.cell(row=r, column=1, value=period_month)
        ws.cell(row=r, column=2, value=line.date.isoformat() if line.date else "")
        ws.cell(row=r, column=3, value=line.party_name or "")
        ws.cell(row=r, column=4, value=line.voucher_number or "")   # entity_number = voucher no
        ws.cell(row=r, column=5, value=line.gstin or "")           # supplier GSTIN from ledger master
        ws.cell(row=r, column=6, value=line.voucher_number or "")   # transaction_id = voucher no
        ws.cell(row=r, column=7, value=line.voucher_type or "")     # transaction_type = voucher type
        ws.cell(row=r, column=8, value=_f(line.taxable))
        ws.cell(row=r, column=9, value=_f(line.igst))
        ws.cell(row=r, column=10, value=_f(line.cgst))
        ws.cell(row=r, column=11, value=_f(line.sgst))
        ws.cell(row=r, column=12, value=f"=I{r}+J{r}+K{r}")         # Total GST
        ws.cell(row=r, column=13, value=f"=L{r}+H{r}")              # Invoice Value
        ws.cell(row=r, column=14, value=f"=L{r}/H{r}")              # GST Rate
        # Clearly-labelled copies (client request). Invoice No. is the Supplier
        # Invoice No. (voucher REFERENCE from the Voucher Register); blank when the
        # voucher has none. Voucher No is the Tally voucher number.
        ws.cell(row=r, column=15, value=line.voucher_type or "")
        ws.cell(row=r, column=16, value=line.voucher_number or "")
        ws.cell(row=r, column=17, value=line.reference or "")
        _style_row(ws, r, 17, _TNR11)  # reference keeps Purchase data in Times New Roman
        r += 1
    last = r - 1
    r += 1
    for col in range(8, 14):  # SUM taxable..invoice value
        letter = ws.cell(row=1, column=col).column_letter
        cell = ws.cell(row=r, column=col, value=f"=SUM({letter}{first}:{letter}{last})" if last >= first else 0)
        cell.font = _TOTAL_FONT
        cell.fill = _ACCENT6
    return r


def _build_abstract(
    ws: Worksheet, detail: TallyGstDetailResult, sales_total: int, purchase_total: int
) -> None:
    """Reproduce the reference Abstract cell-for-cell, formula-driven.

    Left block (cols A-E) holds the month's GST heads; the right block (cols H-K)
    holds the GSTR-3B / GSTR-2B / reclaim reconciliation. GST on purchases/sales
    link to the Purchase/Sales totals; Closing and the GSTR-3B disclosure are the
    client's own formulas. The heads needing opening balances, GSTR-2B or portal
    data are left at 0 for the accountant, with the labels in exact positions.
    """
    def put(row: int, col: int, value, bold: bool = False) -> None:
        cell = ws.cell(row=row, column=col, value=value)
        cell.font = _ABS_BOLD if bold else _ABS_FONT

    # --- Left block: heads of the month -----------------------------------
    put(1, 1, "GST Balance/(Payable) for the month", bold=True)
    put(2, 1, "Particulars", bold=True)
    put(2, 3, "Amount", bold=True)
    put(2, 4, "Offset", bold=True)
    put(2, 5, "Balance", bold=True)
    put(3, 1, "Opening"); put(3, 2, "IGST"); put(3, 3, 0)
    put(4, 2, "CGST"); put(4, 3, 0)
    put(5, 2, "SGST"); put(5, 3, 0)
    put(7, 1, "GST Paid"); put(7, 2, "IGST"); put(7, 3, 0)
    put(8, 2, "CGST"); put(9, 2, "SGST")
    # Linked to the Purchase sheet totals (client request).
    put(12, 1, "GST on purchases"); put(12, 2, "IGST"); put(12, 3, f"=Purchase!I{purchase_total}")
    put(13, 2, "CGST"); put(13, 3, f"=Purchase!J{purchase_total}")
    put(14, 2, "SGST"); put(14, 3, f"=Purchase!K{purchase_total}")
    put(16, 1, "GST on advances"); put(16, 2, "IGST"); put(16, 3, 0)
    put(17, 2, "CGST"); put(17, 3, 0)
    put(18, 2, "SGST"); put(18, 3, 0)
    # Reverse-charge heads link to the RC breakup totals (client formula C20=B48).
    put(20, 1, "GST on Reverse charge"); put(20, 2, "IGST"); put(20, 3, "=B48")
    put(21, 2, "CGST"); put(21, 3, "=C48")
    put(22, 2, "SGST"); put(22, 3, "=D48")
    # Linked to the Sales sheet totals (client request).
    put(24, 1, "GST on sales"); put(24, 2, "IGST"); put(24, 3, f"=Sales!F{sales_total}")
    put(25, 2, "CGST"); put(25, 3, f"=Sales!G{sales_total}")
    put(26, 2, "SGST"); put(26, 3, f"=Sales!H{sales_total}")
    # Closing = Opening + GST Paid + purchases + RCM - advances - sales (client formula).
    put(28, 1, "Closing"); put(28, 2, "IGST")
    put(28, 3, "=C3+C7+C12+C20-C16-C24"); put(28, 4, 0); put(28, 5, "=SUM(C28:D28)")
    put(29, 2, "CGST"); put(29, 3, "=C4+C8+C13+C21-C17-C25"); put(29, 4, 0); put(29, 5, "=SUM(C29:D29)")
    put(30, 2, "SGST"); put(30, 3, "=C5+C9+C14+C22-C18-C26"); put(30, 4, 0); put(30, 5, "=SUM(C30:D30)")
    put(31, 1, "Balance/(Payable)", bold=True)
    put(31, 3, "=SUM(C28:C30)"); put(31, 5, "=SUM(E28:E30)")
    put(32, 1, "Deferred GST"); put(32, 5, "=Deferred!L13")
    put(35, 1, "As per ZOHO")
    # Reverse-charge GL breakup: header + total (the GL names are client data).
    put(38, 1, "Reverse charge", bold=True)
    put(38, 2, "IGST", bold=True); put(38, 3, "CGST", bold=True); put(38, 4, "SGST", bold=True)
    put(48, 1, "Total Taxable Value", bold=True)
    # No RCM GL breakup data in PAL; totals stay 0 (client enters the GL rows).
    put(48, 2, 0, bold=True); put(48, 3, 0, bold=True); put(48, 4, 0, bold=True)
    put(50, 1, "Payable"); put(50, 5, "=C31")
    put(51, 1, "Portal Payable")

    # --- Right block: GSTR-3B / 2B / reclaim reconciliation (client formulas) ---
    put(1, 8, "Portal", bold=True); put(1, 9, "Amount", bold=True)
    put(2, 8, "IGST"); put(3, 8, "CGST"); put(4, 8, "SGST")
    put(5, 9, "=SUM(I2:I4)"); put(6, 9, "=I5-C3-C4-C5")
    put(12, 8, "GSTR - 3B Disclosure", bold=True)
    put(13, 8, "Particulars", bold=True)
    put(13, 9, "IGST", bold=True); put(13, 10, "CGST", bold=True); put(13, 11, "SGST", bold=True)
    put(14, 8, "Opening balance"); put(14, 9, 0); put(14, 10, 0); put(14, 11, 0)
    put(15, 8, "Claimed during the month form OB"); put(15, 9, 0); put(15, 10, 0); put(15, 11, 0)
    put(16, 8, "ITC Lapsed_24-25")
    put(17, 9, "=I14-I15-I16"); put(17, 10, "=J14-J15-J16"); put(17, 11, "=K14-K15-K16")
    put(19, 8, "GSTR - 2B "); put(19, 9, 0); put(19, 10, 0); put(19, 11, 0)
    put(20, 8, "Claim during the month")
    put(20, 9, "=C12"); put(20, 10, "=C13"); put(20, 11, "=C14")
    put(21, 8, "Ineligible")
    put(22, 8, "Not claimed for the month")
    put(22, 9, "=I19-I20"); put(22, 10, "=J19-J20"); put(22, 11, "=K19-K20")
    put(24, 8, "Closing balance of the reclaim ledger")
    put(24, 9, "=I17+I22"); put(24, 10, "=J17+J22"); put(24, 11, "=K17+K22")
    put(26, 8, "Total claim for GSTR3B disclosure")
    put(26, 9, "=I15+I19+I16"); put(26, 10, "=J15+J19+J16"); put(26, 11, "=K15+K19+K16")
    put(29, 8, "Balance as per ZOHO")
    put(30, 8, "Input Tax Credits")
    put(31, 8, "GST Payable")

    # --- Section fills, matching the reference exactly --------------------
    for col in range(1, 6):  # grey 'Particulars' header + Balance/(Payable) rows
        ws.cell(row=2, column=col).fill = _GREY
        ws.cell(row=31, column=col).fill = _GREY
    ws.cell(row=12, column=8).fill = _YELLOW  # 'GSTR - 3B Disclosure'
    for col in range(8, 12):  # light-orange GSTR-3B table header row
        ws.cell(row=13, column=col).fill = _LIGHT_ORANGE
    for col in range(2, 5):  # green Reverse-charge total
        ws.cell(row=48, column=col).fill = _GREEN


def _build_invoice_details(ws: Worksheet, outward: list[TallyGstLine]) -> None:
    numbers = [l.voucher_number for l in outward if l.voucher_number]
    cancelled = sum(1 for l in outward if not l.taxable and not l.total_gst)
    _header(ws, 2, ["Doc type", "Form", "To", "Total", "Cancelled"])
    ws.cell(row=1, column=1, value="").font = _BOLD
    ws.append([
        "Invoices",
        numbers[0] if numbers else "",
        numbers[-1] if numbers else "",
        len(outward),
        cancelled,
    ])
    ws.append(["Credit notes", 0, 0, 0, 0])
    ws.append(["RCM Invoices", "", "", 0, 0])
    ws.append(["Payment Voucher", "", "", 0, 0])


def _build_hsn(ws: Worksheet, rows) -> None:
    """Fill the HSN summary sheet: outward supplies grouped by HSN code + rate."""
    ws.cell(row=1, column=1, value="B2B").font = _BOLD
    _header(ws, 2, [
        "Description", "Quantity", "Unit Of Measure", "HSN Code or SAC",
        "Tax Rate", "Taxable Amount", "IGST Amount", "CGST Amount",
        "SGST Amount", "CESS Amount", "Comment",
    ])
    r = 3
    for row in rows:
        ws.cell(row=r, column=1, value=row.description)
        ws.cell(row=r, column=2, value=_f(row.quantity))
        ws.cell(row=r, column=3, value=row.unit or "")
        ws.cell(row=r, column=4, value=row.hsn or "")
        ws.cell(row=r, column=5, value=_f(row.rate))
        ws.cell(row=r, column=6, value=_f(row.taxable))
        ws.cell(row=r, column=7, value=_f(row.igst))
        ws.cell(row=r, column=8, value=_f(row.cgst))
        ws.cell(row=r, column=9, value=_f(row.sgst))
        ws.cell(row=r, column=10, value=0)
        # Flag groups PAL could not assign an HSN to: the item has none in Tally.
        ws.cell(row=r, column=11, value="" if row.hsn else "HSN not set in Tally")
        _style_row(ws, r, 11, _TNR11)
        r += 1


def _template(ws: Worksheet, headers: list, title_row: str | None = None) -> None:
    """Write a header-only sheet: a labelled template, no data rows."""
    start = 1
    if title_row is not None:
        ws.cell(row=1, column=1, value=title_row).font = _BOLD
        start = 2
    _header(ws, start, headers)


def build_gst_workbook(
    detail: TallyGstDetailResult,
    *,
    company_name: str,
    from_date: date | None,
) -> bytes:
    """Return the GST working as .xlsx bytes in the reference eleven-sheet layout."""
    outward = [l for l in detail.lines if l.direction == "outward"]
    inward = [l for l in detail.lines if l.direction == "inward"]
    period_month = (
        f"{_MONTHS[from_date.month - 1].title()}-{from_date.strftime('%y')}"
        if from_date else ""
    )

    wb = Workbook()
    wb.remove(wb.active)  # drop the default empty sheet

    # Create Abstract first so it stays the first tab, but fill it last: it
    # references the Sales / Purchase total rows, known only after those build.
    abstract_ws = wb.create_sheet("Abstract")
    sales_total = _build_sales(wb.create_sheet("Sales"), outward)
    purchase_total = _build_purchase(wb.create_sheet("Purchase"), inward, period_month)
    _build_abstract(abstract_ws, detail, sales_total, purchase_total)
    _build_invoice_details(wb.create_sheet("Invoice Details"), outward)

    _template(wb.create_sheet("Deferred"), [
        "Month", "Date", "Particulars", "Voucher Type", "Supplier Invoice No.",
        "Supplier Invoice Date", "GSTN", "Taxable Value", "Input IGST",
        "Input CGST", "Input SGST", "Total GST", "Invoice Value", "Status",
        "Concate",
    ])
    _template(wb.create_sheet("Reverse charge"), [
        "Date", "Particulars", "Voucher Type", "RCM Invoice", "Payment Voucher",
        "Voucher No.", "Amount", "IGST", "CGST", "SGST", "GL",
    ])
    _template(wb.create_sheet("E-Inv"), [
        "GSTIN/UIN of Recipient", "Receiver Name", "Invoice number",
        "Invoice date", "Invoice value", "Place of Supply", "Reverse Charge",
        "Applicable % of Tax Rate", "Invoice Type", "E-Commerce GSTIN", "Rate",
        "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax",
        "Cess Amount", "IRN", "IRN date", "E-invoice status",
    ], title_row="E-invoice details auto-populated for Form GSTR-1")
    _template(wb.create_sheet("E-invoice reco"), [
        "GSTN", "Taxable Value", "IGST", "CGST", "SGST", "", "Taxable Value",
        "IGST", "CGST", "SGST", "", "Taxable Value", "IGST", "CGST", "SGST",
    ])
    _build_hsn(wb.create_sheet("HSN"), detail.hsn_rows)
    _template(wb.create_sheet("GST charged off in books"), [
        "Month", "Date", "Particulars", "Voucher Type", "Supplier Invoice No.",
        "Supplier Invoice Date", "GSTIN/UIN", "Taxable Value", "Input IGST",
        "Input CGST", "Input SGST", "Total GST", "Invoice Value", "Status",
        "Concate",
    ])
    _template(wb.create_sheet("Reclaim"), [
        "Month", "GSTIN of supplier", "Trade/Legal name", "Invoice number",
        "Invoice type", "Invoice Date", "Invoice Value(₹)", "Place of supply",
        "Supply Attract Reverse Charge", "Rate(%)", "Taxable Value (₹)",
        "IGST", "CGST", "SGST", "Cess", "GSTR-1/IFF/GSTR-5 Period",
        "GSTR-1/IFF/GSTR-5 Filing Date", "ITC Availability", "Comments", "Concat",
    ])

    for sheet in wb.worksheets:
        _finalize(sheet)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def gst_workbook_filename(from_date: date | None) -> str:
    """Filename like 'GST SEP 2026 v.02.xlsx', matching the reference naming."""
    if from_date:
        return f"GST {_MONTHS[from_date.month - 1]} {from_date.year} v.02.xlsx"
    return "GST Working v.02.xlsx"
