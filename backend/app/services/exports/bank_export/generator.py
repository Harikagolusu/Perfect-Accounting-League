"""Generate the client bank-voucher workbook."""

from __future__ import annotations

from app.services.exports.bank_export.schemas import (
    BANK_COLUMNS,
    BANK_SHEET,
    BANK_TEMPLATE,
    voucher_rows,
)
from app.services.exports.common.models import Voucher
from app.services.exports.common.workbook import (
    load_template,
    prepare_data_sheet,
    set_data_filter,
    workbook_bytes,
    write_rows,
)

NUMBER_FORMATS: dict[int, str] = {
    7: "#,##0.00",
    10: "0.###",
    11: "#,##0.00",
    13: "#,##0.00",
}


def generate_bank_workbook(vouchers: tuple[Voucher, ...]) -> bytes:
    workbook = load_template(BANK_TEMPLATE)
    sheet, styles = prepare_data_sheet(workbook, BANK_SHEET, sample_row=2)

    headers = tuple(cell.value for cell in sheet[1][: len(BANK_COLUMNS)])
    if headers != BANK_COLUMNS:
        raise ValueError(
            "The bundled bank reference template no longer matches the client contract."
        )

    rows = [row for voucher in vouchers for row in voucher_rows(voucher)]
    values = [row.as_values() for row in rows]
    write_rows(
        sheet,
        values,
        styles=styles,
        number_formats=NUMBER_FORMATS,
        wrap_columns=(4, 5, 6, 15),
    )
    set_data_filter(sheet, len(BANK_COLUMNS), len(values))
    return workbook_bytes(workbook)


__all__ = ["generate_bank_workbook"]
