"""Generate the client invoice workbook from canonical rows."""

from __future__ import annotations

from app.services.exports.common.workbook import (
    load_template,
    prepare_data_sheet,
    set_data_filter,
    workbook_bytes,
    write_rows,
)
from app.services.exports.invoice_export.schemas import (
    INVOICE_COLUMNS,
    INVOICE_SHEET,
    INVOICE_TEMPLATE,
    InvoiceExportRow,
)

NUMBER_FORMATS: dict[int, str] = {
    1: "dd/mm/yyyy",
    12: "0.###",
    13: "0.00",
    14: "#,##0.00",
    15: "#,##0.00",
    16: "#,##0.00",
    17: "#,##0.00",
    18: "#,##0.00",
}


def generate_invoice_workbook(rows: tuple[InvoiceExportRow, ...]) -> bytes:
    workbook = load_template(INVOICE_TEMPLATE)
    sheet, styles = prepare_data_sheet(workbook, INVOICE_SHEET, sample_row=2)

    headers = tuple(cell.value for cell in sheet[1][: len(INVOICE_COLUMNS)])
    if headers != INVOICE_COLUMNS:
        raise ValueError(
            "The bundled invoice template no longer matches the client contract."
        )

    values = [row.as_values() for row in rows]
    write_rows(
        sheet,
        values,
        styles=styles,
        number_formats=NUMBER_FORMATS,
        wrap_columns=(8, 9),
    )
    set_data_filter(sheet, len(INVOICE_COLUMNS), len(values))
    return workbook_bytes(workbook)


__all__ = ["generate_invoice_workbook"]
