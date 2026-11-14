"""Generate the client's monthly TDS working workbook from a raw export.

The client fixed the layout (TDS_Sep-26 V.01.xlsx): three sheets -- "Non
Salary", "Salary", "PL sep". This script takes a workbook holding only the raw
line data for a month and writes the same three sheets with every total and
cross-sheet reconciliation computed, so the monthly job is: export -> fill the
raw sheets -> run this.

Nothing here reads PAL's database. PAL stores no TDS amount, section, salary or
GL-wise P&L, so the numbers come from the export the user already prepares
(Zoho / Tally / a hand-filled sheet).

Input workbook (sheet names must match; columns are found by header text, so
their order may vary -- except "PL sep", read by position because its amount
header is the period date):

  Non Salary : Date, Vendor, Transactions, PAN, Transaction Type, Tax base,
               TDS, TDS %, Section, Account type
  Salary     : Sl no, Emp. No, Name of the employee, PAN, GROSS, IT
  PL sep     : col A GL name, B amount, C TDS apply, D TDS do not apply
  Params     : optional key/value sheet; keys zoho_net_payable, zoho_pl_apply
               (the Zoho control totals to reconcile against). CLI flags win.

Computation (matches V.01):
  Non Salary F20/G20 = tax-base / TDS subtotal of in-period bills only.
             F21/G21 = Salary sheet GROSS / IT totals.
             G22 Total = G20+G21, G23 Net Payable = G22,
             G24 = Zoho net payable, G25 = G22-G24 (note when non-zero).
  Salary     E34/F34 = GROSS / IT column totals.
  PL sep     row 29  = B/C/D/E column totals, E(row) = B-C-D,
             C30 = Zoho TDS-apply total, C31 = C30-C29.

A bill dated outside the period is still listed but left out of F20/G20, exactly
as the straggler row in V.01 is. Pass --period to set the month; without it,
every bill counts.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

ZERO = Decimal("0")

# Number formats copied from the client's V.01 workbook so output matches it.
MONEY_FMT = '_ * #,##0_ ;_ * \\-#,##0_ ;_ * "-"??_ ;_ @_ '  # Non Salary + PL
SALARY_MONEY_FMT = '_(* #,##0_);_(* \\(#,##0\\);_(* "-"??_);_(@_)'  # Salary GROSS
PCT_FMT = "0%"
IT_FMT = "#,##0"  # Salary IT column in V.01
DATE_HDR_FMT = "mmm-yy"

NON_SALARY = "Non Salary"
SALARY = "Salary"
PL_SEP = "PL sep"

NON_SALARY_HEADERS = [
    "Date", "Vendor", "Transactions", "PAN", "Transaction Type",
    "Tax base", "TDS", "TDS %", "Section", "Account type",
]
SALARY_HEADERS = ["Sl no", "Emp. No", "Name of the employee", "PAN", "GROSS", "IT"]

_HEADER = Font(bold=True)
_TOTAL = Font(bold=True)
_HEADER_FILL = PatternFill("solid", fgColor="F5F5F5")
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _dec(value) -> Decimal:
    """Coerce a cell value to Decimal; blank/garbage -> 0."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        # str(float) keeps the stored value; Decimal(float) would add noise.
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ZERO


def _parse_date(value) -> date | None:
    """Parse the mixed date forms seen in the client file.

    Handles datetime cells, ISO 'YYYY-MM-DD', and day-first 'DD/MM/YYYY' or
    'DD-M-YYYY'. Returns None when unparseable (e.g. a label like 'Sep26').
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", text)
    if m:
        d, mth, y = (int(g) for g in m.groups())
        try:
            return date(y, mth, d)
        except ValueError:
            return None
    return None


def _header_index(ws: Worksheet, wanted: list[str]) -> dict[str, int]:
    """Map each wanted header to its 1-based column, by case-insensitive text."""
    present = {
        str(c.value).strip().lower(): c.column
        for c in ws[1]
        if c.value is not None
    }
    index: dict[str, int] = {}
    for name in wanted:
        col = present.get(name.lower())
        if col is None:
            raise ValueError(f"sheet {ws.title!r} missing column {name!r}")
        index[name] = col
    return index


@dataclass
class Bill:
    date: date | None
    vendor: object
    transactions: object
    pan: object
    txn_type: object
    tax_base: Decimal
    tds: Decimal
    tds_pct: Decimal | None
    section: object
    account_type: object
    raw_date: object


@dataclass
class Employee:
    sl_no: object
    emp_no: object
    name: object
    pan: object
    gross: Decimal
    it: Decimal


@dataclass
class PLRow:
    gl: object
    amount: Decimal
    tds_apply: Decimal
    tds_not: Decimal


def _read_bills(ws: Worksheet) -> list[Bill]:
    idx = _header_index(ws, NON_SALARY_HEADERS)
    bills: list[Bill] = []
    for row in range(2, ws.max_row + 1):
        def cell(name):
            return ws.cell(row=row, column=idx[name]).value

        raw_date = cell("Date")
        base = cell("Tax base")
        # Stop at the footer / first empty data row: no date and no tax base.
        if raw_date in (None, "") and base in (None, ""):
            break
        pct = cell("TDS %")
        bills.append(
            Bill(
                date=_parse_date(raw_date),
                vendor=cell("Vendor"),
                transactions=cell("Transactions"),
                pan=cell("PAN"),
                txn_type=cell("Transaction Type"),
                tax_base=_dec(base),
                tds=_dec(cell("TDS")),
                tds_pct=_dec(pct) if pct not in (None, "") else None,
                section=cell("Section"),
                account_type=cell("Account type"),
                raw_date=raw_date,
            )
        )
    return bills


def _read_employees(ws: Worksheet) -> list[Employee]:
    idx = _header_index(ws, SALARY_HEADERS)
    out: list[Employee] = []
    for row in range(2, ws.max_row + 1):
        def cell(name):
            return ws.cell(row=row, column=idx[name]).value

        name = cell("Name of the employee")
        sl = cell("Sl no")
        if name in (None, "") and sl in (None, ""):
            break
        if isinstance(name, str) and name.strip().lower() == "total":
            break
        out.append(
            Employee(
                sl_no=sl,
                emp_no=cell("Emp. No"),
                name=name,
                pan=cell("PAN"),
                gross=_dec(cell("GROSS")),
                it=_dec(cell("IT")),
            )
        )
    return out


def _read_pl(ws: Worksheet) -> list[PLRow]:
    # Positional: col B header is the period date, so headers are not reliable.
    out: list[PLRow] = []
    for row in range(2, ws.max_row + 1):
        gl = ws.cell(row=row, column=1).value
        amount = ws.cell(row=row, column=2).value
        # A data row always names a GL; the totals row below does not.
        if gl in (None, ""):
            break
        out.append(
            PLRow(
                gl=gl,
                amount=_dec(amount),
                tds_apply=_dec(ws.cell(row=row, column=3).value),
                tds_not=_dec(ws.cell(row=row, column=4).value),
            )
        )
    return out


def _read_params(wb) -> dict[str, Decimal]:
    if "Params" not in wb.sheetnames:
        return {}
    ws = wb["Params"]
    params: dict[str, Decimal] = {}
    for row in range(1, ws.max_row + 1):
        key = ws.cell(row=row, column=1).value
        val = ws.cell(row=row, column=2).value
        if isinstance(key, str) and key.strip():
            params[key.strip().lower()] = _dec(val)
    return params


@dataclass
class Working:
    bills: list[Bill]
    employees: list[Employee]
    pl: list[PLRow]
    period: tuple[int, int] | None  # (year, month)
    zoho_net_payable: Decimal | None
    zoho_pl_apply: Decimal | None

    def in_period(self, bill: Bill) -> bool:
        if self.period is None:
            return True
        if bill.date is None:
            return False
        return (bill.date.year, bill.date.month) == self.period

    # ---- computed footers -------------------------------------------------
    @property
    def bill_base_total(self) -> Decimal:
        return sum((b.tax_base for b in self.bills if self.in_period(b)), ZERO)

    @property
    def bill_tds_total(self) -> Decimal:
        return sum((b.tds for b in self.bills if self.in_period(b)), ZERO)

    @property
    def salary_gross_total(self) -> Decimal:
        return sum((e.gross for e in self.employees), ZERO)

    @property
    def salary_it_total(self) -> Decimal:
        return sum((e.it for e in self.employees), ZERO)

    @property
    def total_tds(self) -> Decimal:
        return self.bill_tds_total + self.salary_it_total

    @property
    def pl_amount_total(self) -> Decimal:
        return sum((r.amount for r in self.pl), ZERO)

    @property
    def pl_apply_total(self) -> Decimal:
        return sum((r.tds_apply for r in self.pl), ZERO)

    @property
    def pl_not_total(self) -> Decimal:
        return sum((r.tds_not for r in self.pl), ZERO)


def load(input_path: Path, period: tuple[int, int] | None,
         zoho_net_payable: Decimal | None,
         zoho_pl_apply: Decimal | None) -> Working:
    wb = openpyxl.load_workbook(input_path, data_only=True)
    for name in (NON_SALARY, SALARY, PL_SEP):
        if name not in wb.sheetnames:
            raise ValueError(f"input is missing sheet {name!r}")
    params = _read_params(wb)
    return Working(
        bills=_read_bills(wb[NON_SALARY]),
        employees=_read_employees(wb[SALARY]),
        pl=_read_pl(wb[PL_SEP]),
        period=period,
        zoho_net_payable=(
            zoho_net_payable if zoho_net_payable is not None
            else params.get("zoho_net_payable")
        ),
        zoho_pl_apply=(
            zoho_pl_apply if zoho_pl_apply is not None
            else params.get("zoho_pl_apply")
        ),
    )


def load_csv(csv_path: Path, period: tuple[int, int] | None,
             zoho_net_payable: Decimal | None,
             zoho_pl_apply: Decimal | None) -> Working:
    """Load PAL's flat TDS CSV export as the Non Salary bills.

    PAL's TDS page exports one row per voucher with columns VoucherDate,
    VoucherNo, Vendor, PAN, Section, Gross, Base, Rate, Due, Deducted,
    Variance, Status. That is the Non Salary data only; Salary and PL sep have
    no source here and come out empty, to be filled from their own exports.

    The client's TDS column holds the TDS amount, so ``Due`` maps to it. ``Rate``
    is a whole-number percent (2 = 2%); the client stores it as a fraction, so
    it is divided by 100.
    """
    bills: list[Bill] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        lower = {name.strip().lower(): name for name in (reader.fieldnames or [])}

        def col(row, key):
            src = lower.get(key)
            return row.get(src) if src else None

        for row in reader:
            raw_date = col(row, "voucherdate")
            base = col(row, "base")
            if not raw_date and not base:
                continue
            rate = col(row, "rate")
            rate_dec = _dec(rate)
            bills.append(
                Bill(
                    date=_parse_date(raw_date),
                    vendor=col(row, "vendor"),
                    transactions=col(row, "voucherno"),
                    pan=col(row, "pan"),
                    txn_type="bill",
                    tax_base=_dec(base),
                    tds=_dec(col(row, "due")),
                    tds_pct=(rate_dec / 100 if rate_dec else None),
                    section=col(row, "section"),
                    account_type=None,
                    raw_date=raw_date,
                )
            )
    return Working(
        bills=bills, employees=[], pl=[], period=period,
        zoho_net_payable=zoho_net_payable, zoho_pl_apply=zoho_pl_apply,
    )


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _write_header(ws: Worksheet, labels: list[str]) -> None:
    for col, label in enumerate(labels, start=1):
        c = ws.cell(row=1, column=col, value=label)
        c.font = _HEADER
        c.fill = _HEADER_FILL
        c.border = _BORDER


def _build_non_salary(ws: Worksheet, w: Working) -> None:
    _write_header(ws, NON_SALARY_HEADERS)
    for i, b in enumerate(w.bills):
        r = 2 + i
        ws.cell(row=r, column=1, value=b.raw_date)
        ws.cell(row=r, column=2, value=b.vendor)
        ws.cell(row=r, column=3, value=b.transactions)
        ws.cell(row=r, column=4, value=b.pan)
        ws.cell(row=r, column=5, value=b.txn_type)
        ws.cell(row=r, column=6, value=_f(b.tax_base)).number_format = MONEY_FMT
        ws.cell(row=r, column=7, value=_f(b.tds)).number_format = MONEY_FMT
        pct = ws.cell(row=r, column=8, value=_f(b.tds_pct))
        pct.number_format = PCT_FMT
        ws.cell(row=r, column=9, value=b.section)
        ws.cell(row=r, column=10, value=b.account_type)

    # Footer sits three blank rows below the last bill, exactly as V.01 (15
    # bills end at row 16, subtotal at row 20).
    base = 2 + len(w.bills) + 3
    rows = {
        "subtotal": base,          # bills subtotal (in period)
        "salary": base + 1,        # salary totals carried in
        "total": base + 2,
        "net": base + 3,
        "zoho": base + 4,
        "diff": base + 5,
    }
    ws.cell(row=rows["subtotal"], column=6,
            value=_f(w.bill_base_total)).number_format = MONEY_FMT
    ws.cell(row=rows["subtotal"], column=7,
            value=_f(w.bill_tds_total)).number_format = MONEY_FMT
    ws.cell(row=rows["salary"], column=6,
            value=_f(w.salary_gross_total)).number_format = MONEY_FMT
    ws.cell(row=rows["salary"], column=7,
            value=_f(w.salary_it_total)).number_format = MONEY_FMT

    def label_total(row: int, label: str, value: Decimal | None) -> None:
        lc = ws.cell(row=row, column=4, value=label)
        lc.font = _TOTAL
        vc = ws.cell(row=row, column=7, value=_f(value))
        vc.number_format = MONEY_FMT
        vc.font = _TOTAL

    label_total(rows["total"], "Total", w.total_tds)
    label_total(rows["net"], "Net Payable", w.total_tds)
    if w.zoho_net_payable is not None:
        label_total(rows["zoho"], "Zoho", w.zoho_net_payable)
        diff = w.total_tds - w.zoho_net_payable
        label_total(rows["diff"], "Difference", diff)
        if abs(diff) > Decimal("0.005"):
            ws.cell(row=rows["diff"], column=8,
                    value="Difference to be reconciled against Zoho")


def _build_salary(ws: Worksheet, w: Working) -> None:
    _write_header(ws, SALARY_HEADERS)
    for i, e in enumerate(w.employees):
        r = 2 + i
        ws.cell(row=r, column=1, value=e.sl_no)
        ws.cell(row=r, column=2, value=e.emp_no)
        ws.cell(row=r, column=3, value=e.name)
        ws.cell(row=r, column=4, value=e.pan)
        ws.cell(row=r, column=5, value=_f(e.gross)).number_format = SALARY_MONEY_FMT
        # IT column uniformly #,##0. V.01's own per-row formats are inconsistent
        # (value 1 sometimes #,##0, real values sometimes General) — not matched.
        ws.cell(row=r, column=6, value=_f(e.it)).number_format = IT_FMT
    r = 2 + len(w.employees)
    tc = ws.cell(row=r, column=3, value="TOTAL")
    tc.font = _TOTAL
    gc = ws.cell(row=r, column=5, value=_f(w.salary_gross_total))
    gc.number_format = SALARY_MONEY_FMT
    gc.font = _TOTAL
    ic = ws.cell(row=r, column=6, value=_f(w.salary_it_total))
    ic.number_format = IT_FMT
    ic.font = _TOTAL


def _build_pl(ws: Worksheet, w: Working, period_label) -> None:
    ws.cell(row=1, column=1, value="GL").font = _HEADER
    b1 = ws.cell(row=1, column=2, value=period_label)
    b1.font = _HEADER
    if isinstance(period_label, date):
        b1.number_format = DATE_HDR_FMT
    ws.cell(row=1, column=3, value="TDS apply").font = _HEADER
    ws.cell(row=1, column=4, value="TDS do not apply").font = _HEADER
    ws.cell(row=1, column=5, value="Difference").font = _HEADER
    for i, p in enumerate(w.pl):
        r = 2 + i
        ws.cell(row=r, column=1, value=p.gl)
        ws.cell(row=r, column=2, value=_f(p.amount)).number_format = MONEY_FMT
        ws.cell(row=r, column=3, value=_f(p.tds_apply)).number_format = MONEY_FMT
        ws.cell(row=r, column=4, value=_f(p.tds_not)).number_format = MONEY_FMT
        diff = p.amount - p.tds_apply - p.tds_not
        ws.cell(row=r, column=5, value=_f(diff)).number_format = MONEY_FMT
    total_row = 2 + len(w.pl)
    diff_total = w.pl_amount_total - w.pl_apply_total - w.pl_not_total
    for col, val in (
        (2, w.pl_amount_total), (3, w.pl_apply_total),
        (4, w.pl_not_total), (5, diff_total),
    ):
        c = ws.cell(row=total_row, column=col, value=_f(val))
        c.number_format = MONEY_FMT
        c.font = _TOTAL
    # V.01 puts the Zoho control total and its difference in column C only, with
    # no row labels in column A.
    if w.zoho_pl_apply is not None:
        zc = ws.cell(row=total_row + 1, column=3, value=_f(w.zoho_pl_apply))
        zc.number_format = MONEY_FMT
        dc = ws.cell(row=total_row + 2, column=3,
                     value=_f(w.zoho_pl_apply - w.pl_apply_total))
        dc.number_format = MONEY_FMT


def _autosize(ws: Worksheet) -> None:
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None),
                    default=8)
        ws.column_dimensions[col[0].column_letter].width = min(width + 2, 48)


def build(w: Working, output_path: Path, period_label) -> None:
    wb = openpyxl.Workbook()
    ns = wb.active
    ns.title = NON_SALARY
    _build_non_salary(ns, w)
    _build_salary(wb.create_sheet(SALARY), w)
    _build_pl(wb.create_sheet(PL_SEP), w, period_label)
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        _autosize(ws)
    wb.save(output_path)


def _period_arg(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})$", text.strip())
    if not m:
        raise argparse.ArgumentTypeError("period must be YYYY-MM, e.g. 2026-09")
    return int(m.group(1)), int(m.group(2))


def _self_check(reference: Path) -> int:
    """Reproduce the reference file's own data and assert footers match it.

    Confirms the computation against a known-good workbook (the client's V.01).
    Compares only computed totals, not the pass-through Zoho control figures.
    """
    ref = openpyxl.load_workbook(reference, data_only=True)
    w = load(reference, period=(2026, 9), zoho_net_payable=None,
             zoho_pl_apply=None)
    expected = {
        "bill base (F20)": (Decimal("3265510.8"), w.bill_base_total),
        "bill tds (G20)": (Decimal("109551.70000000001"), w.bill_tds_total),
        "salary gross (F21/E34)": (Decimal("3098006"), w.salary_gross_total),
        "salary it (G21/F34)": (ref[SALARY].cell(row=34, column=6).value,
                                 w.salary_it_total),
        "total tds (G22)": (ref[NON_SALARY].cell(row=22, column=7).value,
                            w.total_tds),
        "pl amount (B29)": (ref[PL_SEP].cell(row=29, column=2).value,
                            w.pl_amount_total),
        "pl apply (C29)": (ref[PL_SEP].cell(row=29, column=3).value,
                           w.pl_apply_total),
        "pl not (D29)": (ref[PL_SEP].cell(row=29, column=4).value,
                         w.pl_not_total),
    }
    ok = True
    for label, (want, got) in expected.items():
        want_d = _dec(want)
        if abs(want_d - got) > Decimal("0.01"):
            print(f"  MISMATCH {label}: expected {want_d}, got {got}")
            ok = False
        else:
            print(f"  ok {label}: {got}")
    print("self-check PASSED" if ok else "self-check FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="raw export workbook (.xlsx)")
    ap.add_argument("output", type=Path, nargs="?",
                    help="output workbook (.xlsx)")
    ap.add_argument("--period", type=_period_arg, default=None,
                    help="working month YYYY-MM; bills outside it are listed "
                         "but excluded from the totals")
    ap.add_argument("--zoho-net-payable", type=Decimal, default=None,
                    help="Zoho TDS payable control total (Non Salary G24)")
    ap.add_argument("--zoho-pl-apply", type=Decimal, default=None,
                    help="Zoho TDS-apply control total (PL sep C30)")
    ap.add_argument("--check", action="store_true",
                    help="reproduce INPUT's own data and assert its footers")
    args = ap.parse_args(argv)

    if args.check:
        return _self_check(args.input)
    if args.output is None:
        ap.error("output path is required (unless --check)")
    loader = load_csv if args.input.suffix.lower() == ".csv" else load
    w = loader(args.input, args.period, args.zoho_net_payable, args.zoho_pl_apply)
    # PL sep's amount header is the period month as a date (mmm-yy in V.01).
    period_label = date(args.period[0], args.period[1], 1) if args.period else "Amount"
    build(w, args.output, period_label)
    print(f"wrote {args.output}")
    print(f"  bills in period : {sum(1 for b in w.bills if w.in_period(b))}"
          f" / {len(w.bills)} listed")
    print(f"  total TDS (G22) : {w.total_tds}")
    if w.zoho_net_payable is not None:
        print(f"  Zoho difference : {w.total_tds - w.zoho_net_payable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
