"""Structured text extraction for XLSX and DOCX invoice documents.

Spreadsheets and word-processing files are read as *text*, not images. Only the
cells and paragraphs that can plausibly hold header data are sent on, and hard
limits keep a large unrelated workbook from becoming a huge prompt:

* at most a handful of sheets, rows, columns and cells;
* at most a bounded number of characters in total, and in any one field;
* a ZIP container that expands beyond a safe budget is rejected before parsing.

When a limit is reached the content is truncated with an explicit marker rather
than silently pretending the document ended. No value is calculated or
corrected here; the text model still has to read it under the existing rules.
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

from app.services.document_errors import DocumentPreparationError

logger = logging.getLogger(__name__)

#: Keep a workbook's prompt bounded even when the file is mostly data.
MAX_SPREADSHEET_SHEETS = 5
MAX_SPREADSHEET_ROWS = 200
MAX_SPREADSHEET_COLUMNS = 40
MAX_SPREADSHEET_CELLS = 4000
#: Keep a long DOCX (terms, annexures) from crowding out the invoice header.
MAX_DOCX_PARAGRAPHS = 1000
MAX_DOCX_TABLE_ROWS = 300
MAX_DOCX_TABLE_COLUMNS = 20
#: Absolute prompt-side cap shared by both formats.
MAX_STRUCTURED_CHARACTERS = 60_000
#: A single paragraph or table cell is clipped before the whole document is
#: assembled, so one enormous cell cannot dominate memory or the prompt.
MAX_CELL_CHARACTERS = 2_000
#: XLSX and DOCX are ZIP containers. Check the declared expansion before
#: handing the bytes to a parser, so a small upload cannot become a memory
#: exhaustion attack. Invoice documents sit far below these values.
MAX_ARCHIVE_ENTRIES = 10_000
MAX_UNCOMPRESSED_STRUCTURED_BYTES = 128 * 1024 * 1024

TRUNCATION_MARKER = "\n[Document truncated: processing limit reached]"


@dataclass(frozen=True)
class StructuredText:
    """Text read from a structured document, plus whether it was truncated."""

    text: str
    truncated: bool


def extract_structured_text(extension: str, raw_bytes: bytes) -> StructuredText:
    """Read a spreadsheet or DOCX into bounded plain text.

    Raises `DocumentPreparationError` with a safe message when the file cannot
    be parsed. Legacy `.xls` is rejected explicitly: it is a different binary
    format and silently misreading it would be worse than asking for `.xlsx`.
    """
    if not raw_bytes:
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_DOCUMENT,
            "The stored document is empty.",
        )

    if extension == ".xlsx":
        return _extract_xlsx(raw_bytes)
    if extension == ".xls":
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_UNREADABLE,
            "Legacy .xls spreadsheets are not supported. Save the file as .xlsx "
            "and upload it again.",
        )
    if extension == ".docx":
        return _extract_docx(raw_bytes)
    raise DocumentPreparationError(
        DocumentPreparationError.UNSUPPORTED_DOCUMENT_TYPE,
        "Only XLSX and DOCX documents can be read as structured text.",
    )


def _check_archive(raw_bytes: bytes, *, label: str) -> None:
    """Reject a ZIP container that expands beyond the processing budget."""
    try:
        with zipfile.ZipFile(BytesIO(raw_bytes)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise DocumentPreparationError(
                    DocumentPreparationError.DOCUMENT_TOO_LARGE,
                    "The document contains too many internal files to process.",
                )
            total_size = sum(info.file_size for info in entries)
            if total_size > MAX_UNCOMPRESSED_STRUCTURED_BYTES:
                raise DocumentPreparationError(
                    DocumentPreparationError.DOCUMENT_TOO_LARGE,
                    "The document expands beyond the safe processing limit.",
                )
    except DocumentPreparationError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_UNREADABLE,
            f"The {label} could not be read.",
        ) from exc


def _extract_xlsx(raw_bytes: bytes) -> StructuredText:
    _check_archive(raw_bytes, label="spreadsheet")
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency is declared
        logger.error("openpyxl is not installed")
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_CONFIGURATION,
            "Spreadsheet reading is not available in this deployment.",
        ) from exc

    try:
        workbook = load_workbook(
            BytesIO(raw_bytes), read_only=True, data_only=True
        )
    except Exception as exc:
        logger.warning("Could not read spreadsheet (%s)", type(exc).__name__)
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_UNREADABLE,
            "The spreadsheet could not be read.",
        ) from exc

    try:
        try:
            return _read_workbook(workbook)
        except DocumentPreparationError:
            raise
        except Exception as exc:
            logger.warning(
                "Could not process spreadsheet (%s)", type(exc).__name__
            )
            raise DocumentPreparationError(
                DocumentPreparationError.DOCUMENT_UNREADABLE,
                "The spreadsheet could not be read.",
            ) from exc
    finally:
        workbook.close()


def _read_workbook(workbook: Any) -> StructuredText:
    lines: list[str] = []
    cells_read = 0
    truncated = False
    for sheet_index, worksheet in enumerate(workbook.worksheets):
        if sheet_index >= MAX_SPREADSHEET_SHEETS:
            truncated = True
            break
        sheet_lines: list[str] = []
        exhausted = False
        for row in worksheet.iter_rows(
            max_row=MAX_SPREADSHEET_ROWS,
            max_col=MAX_SPREADSHEET_COLUMNS,
            values_only=True,
        ):
            cells: list[str] = []
            for value in row:
                cell_text, clipped = _clip_text(_cell_text(value))
                truncated = truncated or clipped
                cells.append(cell_text)
            # Drop trailing empty cells; keep internal gaps as separators so
            # column alignment remains readable.
            while cells and not cells[-1]:
                cells.pop()
            if not cells or not any(cells):
                continue
            cells_read += len(cells)
            sheet_lines.append(" | ".join(cells))
            if cells_read >= MAX_SPREADSHEET_CELLS:
                truncated = True
                exhausted = True
                break
        if sheet_lines:
            # Do not emit a title for an empty sheet: a header alone must not
            # look like readable invoice content.
            lines.append(f"--- Sheet: {worksheet.title} ---")
            lines.extend(sheet_lines)
        if exhausted:
            break
        # read_only iteration stops at max_row; compare the sheet's real extent
        # so the truncation is explicit rather than silent.
        if (getattr(worksheet, "max_row", 0) or 0) > MAX_SPREADSHEET_ROWS:
            truncated = True
        if (getattr(worksheet, "max_column", 0) or 0) > MAX_SPREADSHEET_COLUMNS:
            truncated = True
    text, truncated = _bounded_text(lines, truncated=truncated)
    return StructuredText(text=text, truncated=truncated)


def _extract_docx(raw_bytes: bytes) -> StructuredText:
    _check_archive(raw_bytes, label="DOCX document")
    try:
        from docx import Document as DocxDocument
    except ImportError as exc:  # pragma: no cover - dependency is declared
        logger.error("python-docx is not installed")
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_CONFIGURATION,
            "DOCX reading is not available in this deployment.",
        ) from exc

    try:
        document = DocxDocument(BytesIO(raw_bytes))
    except Exception as exc:
        logger.warning("Could not read DOCX (%s)", type(exc).__name__)
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_UNREADABLE,
            "The DOCX document could not be read.",
        ) from exc

    try:
        return _read_docx(document)
    except DocumentPreparationError:
        raise
    except Exception as exc:
        logger.warning("Could not process DOCX (%s)", type(exc).__name__)
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_UNREADABLE,
            "The DOCX document could not be read.",
        ) from exc


def _read_docx(document: Any) -> StructuredText:
    lines: list[str] = []
    truncated = False
    for index, paragraph in enumerate(document.paragraphs):
        if index >= MAX_DOCX_PARAGRAPHS:
            truncated = True
            break
        text, clipped = _clip_text(paragraph.text.strip())
        truncated = truncated or clipped
        if text:
            lines.append(text)

    for table in document.tables:
        # Tables are read even after the paragraph budget is exhausted: an
        # invoice total often lives only in a table at the end of the file.
        lines.append("--- Table ---")
        for row_index, row in enumerate(table.rows):
            if row_index >= MAX_DOCX_TABLE_ROWS:
                truncated = True
                break
            cells: list[str] = []
            for cell in row.cells[:MAX_DOCX_TABLE_COLUMNS]:
                cell_text, clipped = _clip_text(
                    cell.text.strip().replace("\n", " ")
                )
                truncated = truncated or clipped
                cells.append(cell_text)
            while cells and not cells[-1]:
                cells.pop()
            if cells and any(cells):
                lines.append(" | ".join(cells))

    text, truncated = _bounded_text(lines, truncated=truncated)
    return StructuredText(text=text, truncated=truncated)


def _cell_text(value: Any) -> str:
    """Render one cell without inventing formatting or precision."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        # 1180.0 is a float from a numeric cell; "1180" is closer to what the
        # sheet displays and avoids exponent notation for large values.
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value).strip()


def _clip_text(text: str) -> tuple[str, bool]:
    """Keep one field bounded, reporting that clipping happened."""
    if len(text) <= MAX_CELL_CHARACTERS:
        return text, False
    return text[:MAX_CELL_CHARACTERS].rstrip(), True


def _bounded_text(
    lines: list[str], *, truncated: bool
) -> tuple[str, bool]:
    text = "\n".join(lines).strip()
    if len(text) > MAX_STRUCTURED_CHARACTERS:
        text = text[:MAX_STRUCTURED_CHARACTERS].rstrip()
        truncated = True
    if truncated:
        text = f"{text}{TRUNCATION_MARKER}".strip()
    return text, truncated


__all__ = [
    "MAX_ARCHIVE_ENTRIES",
    "MAX_CELL_CHARACTERS",
    "MAX_DOCX_PARAGRAPHS",
    "MAX_DOCX_TABLE_COLUMNS",
    "MAX_DOCX_TABLE_ROWS",
    "MAX_SPREADSHEET_CELLS",
    "MAX_SPREADSHEET_COLUMNS",
    "MAX_SPREADSHEET_ROWS",
    "MAX_SPREADSHEET_SHEETS",
    "MAX_STRUCTURED_CHARACTERS",
    "MAX_UNCOMPRESSED_STRUCTURED_BYTES",
    "StructuredText",
    "TRUNCATION_MARKER",
    "extract_structured_text",
]
