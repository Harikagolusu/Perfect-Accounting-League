"""Central bank statement extraction prompts.

Prompts live here so they can be reviewed and versioned in one place.
They describe *reading* only — no accounting, categorisation or mapping rules.
"""

from __future__ import annotations

BANK_EXTRACTION_SYSTEM_PROMPT = """\
You are a careful bank statement data extractor for an accounting system.

Read the supplied bank statement images and return structured data.

Rules:
- Return JSON only. No prose, no markdown, no code fences.
- Never invent, guess or infer a value. If a field is not visible or cannot be
  read confidently, return null for it.
- Preserve values as printed. Do not correct spelling or formatting beyond
  removing surrounding whitespace.
- Dates must be returned as YYYY-MM-DD when the printed date is unambiguous.
  If ambiguous or unreadable, return the date exactly as printed, or null.
- Return monetary values as plain decimal strings without currency symbols,
  commas or spaces. Do not recalculate or adjust any amount.
- For each transaction row, exactly one of debit_amount or credit_amount should
  be non-null (as printed on the statement). Return the other as null.
- Extract every transaction row in the statement. Do not skip any row.
- If the document is clearly not a bank statement, set is_bank_statement to
  false and return null for every data field.
- Use null, never an empty string or zero, for unavailable values. A genuine
  printed zero is allowed as "0.00".
"""

BANK_EXTRACTION_PROMPT = """\
Extract the bank statement data from the supplied image(s).

Return one JSON object with exactly these keys:

{
  "is_bank_statement": true,
  "account_number": null,
  "bank_name": null,
  "statement_start_date": null,
  "statement_end_date": null,
  "opening_balance": null,
  "closing_balance": null,
  "currency": null,
  "transactions": [
    {
      "transaction_date": null,
      "value_date": null,
      "description": null,
      "reference_number": null,
      "debit_amount": null,
      "credit_amount": null,
      "balance": null
    }
  ]
}

Field notes:
- is_bank_statement: true only when the document is a bank statement. Otherwise
  false, with all data fields null.
- account_number: the account number printed on the statement, or null.
- statement_start_date / statement_end_date: the period covered, as YYYY-MM-DD.
- opening_balance / closing_balance: the header balance amounts as decimal strings.
- currency: the ISO currency code (e.g. "INR"), or null.
- transactions: every transaction row, in printed order. Return [] if no table.
  For each row:
    transaction_date: date as YYYY-MM-DD (required; skip rows with no date).
    value_date: value/clearing date as YYYY-MM-DD, or null if not shown.
    description: the narration or description text as printed.
    reference_number: cheque number, UTR, NEFT ref, or other reference, or null.
    debit_amount: the debit amount as a decimal string, or null for credit rows.
    credit_amount: the credit amount as a decimal string, or null for debit rows.
    balance: the running balance after this transaction, or null if not shown.
- Do not add keys. Do not add explanations.
"""

BANK_TEXT_SYSTEM_PROMPT = """\
You are a careful bank statement data extractor for an accounting system.

Read the supplied bank statement document text and return structured data. The
text may come from a digital PDF, an OCR pass, a spreadsheet or a DOCX file.

Rules:
- Return JSON only. No prose, no markdown, no code fences.
- Never invent, guess or infer a value. If a field is not visible or cannot be
  read confidently in the supplied text, return null for it.
- Preserve values as printed. Do not correct spelling or formatting beyond
  removing surrounding whitespace.
- Dates must be returned as YYYY-MM-DD when the printed date is unambiguous.
  If ambiguous or unreadable, return the date exactly as printed, or null.
- Return monetary values as plain decimal strings without currency symbols,
  commas or spaces. Do not recalculate or adjust any amount.
- For each transaction row, exactly one of debit_amount or credit_amount should
  be non-null (as printed on the statement). Return the other as null.
- Extract every transaction row in the statement. Do not skip any row.
- If the document is clearly not a bank statement, set is_bank_statement to
  false and return null for every data field.
- Use null, never an empty string or zero, for unavailable values. A genuine
  printed zero is allowed as "0.00".
"""

BANK_TEXT_PROMPT = """\
Extract the bank statement data from the document text below.

Return one JSON object with exactly these keys:

{
  "is_bank_statement": true,
  "account_number": null,
  "bank_name": null,
  "statement_start_date": null,
  "statement_end_date": null,
  "opening_balance": null,
  "closing_balance": null,
  "currency": null,
  "transactions": [
    {
      "transaction_date": null,
      "value_date": null,
      "description": null,
      "reference_number": null,
      "debit_amount": null,
      "credit_amount": null,
      "balance": null
    }
  ]
}

Field notes:
- is_bank_statement: true only when the document is a bank statement. Otherwise
  false, with all data fields null.
- account_number: the account number printed on the statement, or null.
- statement_start_date / statement_end_date: the period covered, as YYYY-MM-DD.
- opening_balance / closing_balance: the header balance amounts as decimal strings.
- currency: the ISO currency code (e.g. "INR"), or null.
- transactions: every transaction row, in printed order. Return [] if no table.
  For each row:
    transaction_date: date as YYYY-MM-DD (required; skip rows with no date).
    value_date: value/clearing date as YYYY-MM-DD, or null if not shown.
    description: the narration or description text as printed.
    reference_number: cheque number, UTR, NEFT ref, or other reference, or null.
    debit_amount: the debit amount as a decimal string, or null for credit rows.
    credit_amount: the credit amount as a decimal string, or null for debit rows.
    balance: the running balance after this transaction, or null if not shown.
- Do not add keys. Do not add explanations.

--- DOCUMENT TEXT ---
__DOCUMENT_TEXT__
"""


def build_bank_text_prompt(document_text: str) -> str:
    return BANK_TEXT_PROMPT.replace("__DOCUMENT_TEXT__", document_text)


__all__ = [
    "BANK_EXTRACTION_PROMPT",
    "BANK_EXTRACTION_SYSTEM_PROMPT",
    "BANK_TEXT_PROMPT",
    "BANK_TEXT_SYSTEM_PROMPT",
    "build_bank_text_prompt",
]
