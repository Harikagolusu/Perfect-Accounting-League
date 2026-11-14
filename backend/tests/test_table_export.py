"""On-screen table rows -> .xlsx (client: Excel of posted vs pending invoices)."""

import io

from fastapi.testclient import TestClient
from openpyxl import load_workbook


def test_rows_become_a_workbook_with_formulas_neutralised(client: TestClient) -> None:
    response = client.post(
        "/api/v1/exports/table.xlsx",
        json={
            "filename": "Invoice status",
            "sheets": [
                {
                    "name": "Invoices",
                    "columns": ["Invoice No", "Amount", "Status"],
                    "rows": [
                        ["INV-1", 1180.5, "Posted"],
                        ["=HYPERLINK(1)", 10, "1. Vendor not mapped\n2. Check: TDS"],
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert 'filename="Invoice_status.xlsx"' in response.headers["content-disposition"]
    sheet = load_workbook(io.BytesIO(response.content))["Invoices"]
    assert [c.value for c in sheet[1]] == ["Invoice No", "Amount", "Status"]
    assert sheet["B2"].value == 1180.5
    assert sheet["A3"].value == "'=HYPERLINK(1)"
    # Numbered review reasons stay readable as lines in one cell.
    assert sheet["C3"].alignment.wrap_text is True
