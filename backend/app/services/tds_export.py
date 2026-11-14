"""Build the TDS working workbook in the client's three-sheet layout.

Mirrors the accountant's reference file (TDS <Mon>-<YY> V.01): sheets
"Non Salary", "Salary", "PL sep". Non Salary is filled from the Tally TDS
deductions PAL can read; Salary and PL sep need data PAL does not hold and are
written as empty, labelled templates for the accountant to complete.

Read-only: consumes already-computed Tally TDS lines and returns .xlsx bytes.
Number formats are copied from the client's V.01 so the output matches it.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from app.services.tally_sync import TallyTdsLine

# Formats copied from the client's V.01 workbook.
_MONEY = '_ * #,##0_ ;_ * \\-#,##0_ ;_ * "-"??_ ;_ @_ '
_PCT = "0%"
_DATE_HDR = "mmm-yy"

# Fonts and fills copied from the client's V.01 workbook.
_TNR = "Times New Roman"
_HEADER_FONT = Font(name=_TNR, size=11, bold=True)
_TOTAL_FONT = Font(name=_TNR, size=11, bold=True)
_HEADER_FILL = PatternFill("solid", fgColor="FFF5F5F5")       # Non Salary / PL header
_HEADER_FILL_SALARY = PatternFill("solid", fgColor="FFBFBFBF")  # Salary header
_YELLOW = PatternFill("solid", fgColor="FFFFFF00")           # bills subtotal
_GREEN = PatternFill("solid", fgColor="FF92D050")            # totals
_PINK = PatternFill("solid", fgColor="FFFFC6C6")             # Net Payable

_NON_SALARY_HEADERS = [
    "Date", "Particulars", "Voucher Type", "Voucher No.", "Supplier Invoice No.",
    "PAN", "Taxable Value", "Total TDS", "Rate", "New Section", "Expense Ledger",
    "Company/non Company", "TDS Challan Segregation", "TDS Ledger",
]
_SALARY_HEADERS = ["Sl no", "Emp. No", "Name of the employee", "PAN", "GROSS", "IT"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _f(value: Decimal | None) -> float:
    return float(value or 0)


def _header(ws: Worksheet, labels: list[str], fill: PatternFill = _HEADER_FILL) -> None:
    for col, label in enumerate(labels, 1):
        c = ws.cell(row=1, column=col, value=label)
        c.font = _HEADER_FONT
        c.fill = fill


def _build_non_salary(ws: Worksheet, lines: list[TallyTdsLine]) -> None:
    _header(ws, _NON_SALARY_HEADERS)
    for i, line in enumerate(lines):
        r = 2 + i
        pan = line.pan or ""
        # Entity-type letter (PAN 4th char): C=company, else firm/individual.
        entity = pan[3] if len(pan) >= 4 else ""
        ws.cell(row=r, column=1, value=line.voucher_date.isoformat() if line.voucher_date else "")
        ws.cell(row=r, column=2, value=line.party_name or "")
        ws.cell(row=r, column=3, value=line.voucher_type or "")
        ws.cell(row=r, column=4, value=line.voucher_number or "")
        ws.cell(row=r, column=5, value=line.supplier_invoice or "")
        ws.cell(row=r, column=6, value=pan)
        ws.cell(row=r, column=7, value=_f(line.base)).number_format = _MONEY
        ws.cell(row=r, column=8, value=_f(line.tds)).number_format = _MONEY
        # Rate and Company are formula-driven, exactly like the client file.
        ws.cell(row=r, column=9, value=f"=H{r}/G{r}").number_format = _PCT
        ws.cell(row=r, column=10, value=line.new_section or line.section)
        ws.cell(row=r, column=11, value=line.expense_ledger or "")
        ws.cell(row=r, column=12, value=f"=RIGHT(LEFT(F{r},4),1)")
        ws.cell(row=r, column=13, value=("Company" if entity == "C" else "Non Company") if pan else "")
        ws.cell(row=r, column=14, value=line.tds_ledger or "")

    # Footer: section subtotals (Non-Salary / Salary / 195) then grand total,
    # two blank rows below the data. Subtotals and the grand total are SUM
    # formulas, exactly like the client file.
    last = 1 + len(lines)  # last data row (header is row 1)
    sub = last + 3

    def section_row(row, label, base_formula, tds_formula):
        lc = ws.cell(row=row, column=6, value=label)
        lc.font = _TOTAL_FONT
        if base_formula is not None:
            bc = ws.cell(row=row, column=7, value=base_formula)
            bc.number_format = _MONEY
            bc.font = _TOTAL_FONT
        tc = ws.cell(row=row, column=8, value=tds_formula)
        tc.number_format = _MONEY
        tc.font = _TOTAL_FONT

    section_row(sub, "Non - Salary",
                f"=SUM(G2:G{last})" if lines else 0,
                f"=SUM(H2:H{last})" if lines else 0)
    section_row(sub + 1, "Salary", None, 0)   # PAL has no payroll source
    section_row(sub + 2, 195, None, 0)         # no s.195 source
    grand = ws.cell(row=sub + 3, column=8, value=f"=SUM(H{sub}:H{sub + 2})")
    grand.number_format = _MONEY
    grand.font = _TOTAL_FONT
    grand.fill = _GREEN


def _build_salary(ws: Worksheet) -> None:
    # Empty template: PAL has no payroll data.
    _header(ws, _SALARY_HEADERS, _HEADER_FILL_SALARY)
    tc = ws.cell(row=2, column=3, value="TOTAL")
    tc.font = _TOTAL_FONT
    ws.cell(row=2, column=5, value=0).number_format = _MONEY
    ws.cell(row=2, column=6, value=0)


def _build_pl(ws: Worksheet, from_date: date | None) -> None:
    # Empty template: PAL has no GL-wise P&L.
    gl = ws.cell(row=1, column=1, value="GL")
    gl.font = _HEADER_FONT
    gl.fill = _HEADER_FILL
    b1 = ws.cell(row=1, column=2,
                 value=date(from_date.year, from_date.month, 1) if from_date else "Amount")
    b1.font = _HEADER_FONT
    b1.fill = _HEADER_FILL
    if from_date:
        b1.number_format = _DATE_HDR
    for col, label in ((3, "TDS apply"), (4, "TDS do not apply"), (5, "Difference")):
        c = ws.cell(row=1, column=col, value=label)
        c.font = _HEADER_FONT
        c.fill = _HEADER_FILL


def _autosize(ws: Worksheet) -> None:
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(width + 2, 48)


def build_tds_workbook(
    lines: list[TallyTdsLine], *, company_name: str, from_date: date | None
) -> bytes:
    """Return the TDS working as .xlsx bytes in the reference three-sheet layout."""
    wb = Workbook()
    ns = wb.active
    ns.title = "Non Salary"
    _build_non_salary(ns, lines)
    _build_salary(wb.create_sheet("Salary"))
    _build_pl(wb.create_sheet("PL sep"), from_date)
    for ws in wb.worksheets:
        # Times New Roman 11 everywhere, preserving the bold headers/totals.
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None:
                    c.font = Font(name=_TNR, size=11, bold=bool(c.font and c.font.bold))
        _autosize(ws)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def tds_workbook_filename(from_date: date | None) -> str:
    """Filename like 'TDS_Sep-26 v.01.xlsx', matching the reference naming."""
    if from_date:
        return f"TDS_{_MONTHS[from_date.month - 1]}-{from_date.strftime('%y')} v.01.xlsx"
    return "TDS_Working v.01.xlsx"
