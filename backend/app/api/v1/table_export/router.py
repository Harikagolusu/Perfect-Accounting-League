"""Turn on-screen tables into a real .xlsx download.

The browser has no XLSX writer, and the page already holds the exact rows and
statuses the user is looking at. Sending them here keeps the report identical
to the screen instead of re-deriving every status on the server. Nothing is
stored; the workbook is built in memory and returned.
"""

from __future__ import annotations

import io

from fastapi import APIRouter, Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from pydantic import BaseModel, Field

from app.services.exports.common.workbook import content_disposition, safe_filename_piece

router = APIRouter()

Cell = str | int | float | bool | None


class SheetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=31)
    columns: list[str] = Field(..., min_length=1, max_length=50)
    rows: list[list[Cell]] = Field(default_factory=list, max_length=20000)


class TableExportRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=120)
    sheets: list[SheetIn] = Field(..., min_length=1, max_length=10)


def _safe(value: Cell) -> Cell:
    # A cell starting with = + - @ would run as a formula in Excel.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


@router.post("/table.xlsx", summary="Download on-screen table rows as XLSX")
def export_table(payload: TableExportRequest) -> Response:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_in in payload.sheets:
        sheet = workbook.create_sheet(safe_filename_piece(sheet_in.name)[:31])
        sheet.append(sheet_in.columns)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in sheet_in.rows:
            sheet.append([_safe(value) for value in row])
            # Multi-line cells (numbered review reasons) need wrapping to read.
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str) and "\n" in cell.value:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
        for column in sheet.columns:
            width = max(
                max((len(part) for part in str(c.value or "").split("\n")), default=0)
                for c in column
            )
            sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 60)
        sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    filename = f"{safe_filename_piece(payload.filename)}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(filename)},
    )
