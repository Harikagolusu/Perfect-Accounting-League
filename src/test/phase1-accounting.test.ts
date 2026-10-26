import { describe, expect, it } from "vitest";

import { toCompany, type CompanyDto } from "@/lib/api/companies";
import { toLedger, toLedgerPayload, type LedgerDto } from "@/lib/api/ledgers";
import {
  toLinePayload,
  toVoucher,
  voucherStatusLabel,
  voucherTypeLabel,
  type VoucherDto,
} from "@/lib/api/vouchers";
import { toTransactionPayload } from "@/lib/api/bankStatements";

const COMPANY_A = "11111111-1111-1111-1111-111111111111";

const LEDGER: LedgerDto = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  company_id: COMPANY_A,
  name: "Freight Inward",
  code: "FREIGHT",
  ledger_type: "Expense",
  parent_group: "Indirect Expenses",
  tax_classification: null,
  source: "MANUAL",
  external_tally_id: null,
  external_zoho_id: null,
  is_active: true,
  created_at: "2026-10-24T06:00:00Z",
  updated_at: "2026-10-24T06:00:00Z",
};

const VOUCHER: VoucherDto = {
  id: "bbbbbbbb-0000-0000-0000-000000000001",
  company_id: COMPANY_A,
  voucher_date: "2026-03-15",
  voucher_type: "PURCHASE",
  voucher_number: "PUR-0001",
  status: "DRAFT",
  narration: "Freight invoice",
  source_type: "INVOICE",
  source_id: "cccccccc-0000-0000-0000-000000000001",
  source_document_id: null,
  currency: "INR",
  created_by: "local-user",
  approved_by: null,
  approved_at: null,
  rejected_by: null,
  rejected_at: null,
  rejection_reason: null,
  posting_reference: null,
  posted_at: null,
  posting_error: null,
  exported_at: null,
  exported_by: null,
  export_count: 0,
  validation_issues: [],
  total_debit: "1000.00",
  total_credit: "1000.00",
  is_balanced: true,
  lines: [
    {
      id: "dddddddd-0000-0000-0000-000000000001",
      company_id: COMPANY_A,
      voucher_id: "bbbbbbbb-0000-0000-0000-000000000001",
      line_number: 1,
      ledger_id: LEDGER.id,
      ledger_name: LEDGER.name,
      side: "Dr",
      amount: "1000.00",
      narration: null,
      line_type: "LEDGER",
      item_name: null,
      hsn_sac: null,
      billed_quantity: null,
      item_rate: null,
      item_rate_per: null,
      item_amount: null,
      tax_type: null,
    },
  ],
  created_at: "2026-10-24T06:00:00Z",
  updated_at: "2026-10-24T06:00:00Z",
};

const COMPANY: CompanyDto = {
  id: COMPANY_A,
  name: "Sahyadri Agro",
  legal_name: "Sahyadri Agro Foods Private Limited",
  gstin: "27AAJCS4821K1Z5",
  pan: "AAJCS4821K",
  email: null,
  phone: null,
  address: null,
  cgst_ledger: "CGST Input",
  sgst_ledger: "SGST Input",
  igst_ledger: "IGST Input",
  cess_ledger: null,
  round_off_ledger: "Round Off",
  sales_ledger: "Sales",
  financial_year: "FY 2026-27",
  status: "ACTIVE",
  created_at: "2026-10-24T06:00:00Z",
  updated_at: "2026-10-24T06:00:00Z",
};

describe("Phase 1 accounting API mapping", () => {
  it("keeps ledger names and nulls exact", () => {
    const ledger = toLedger(LEDGER);
    expect(ledger.companyId).toBe(COMPANY_A);
    expect(ledger.name).toBe("Freight Inward");
    expect(ledger.taxClassification).toBe("");
    expect(toLedgerPayload(ledger)).toEqual({
      name: "Freight Inward",
      code: "FREIGHT",
      ledger_type: "Expense",
      parent_group: "Indirect Expenses",
      tax_classification: null,
      source: "MANUAL",
      is_active: true,
    });
    expect(toLedgerPayload({ isActive: true })).toEqual({ is_active: true });
  });

  it("keeps voucher amounts as decimal strings and explains the lifecycle label", () => {
    const voucher = toVoucher(VOUCHER);
    expect(voucher.totalDebit).toBe("1000.00");
    expect(voucher.lines[0].amount).toBe("1000.00");
    expect(voucher.isBalanced).toBe(true);
    expect(voucherStatusLabel("AWAITING_IMPORT_CONFIRMATION")).toBe("Awaiting import confirmation");
    expect(voucherTypeLabel("CONTRA")).toBe("Contra");
  });

  it("does not send a line id back when a draft is saved", () => {
    const payload = toLinePayload(toVoucher(VOUCHER).lines[0]);
    expect(payload.ledger_id).toBe(LEDGER.id);
    expect(payload.amount).toBe("1000.00");
    expect("id" in payload).toBe(false);
  });

  it("maps company ledger defaults from the backend contract", () => {
    const company = toCompany(COMPANY);
    expect(company.cgstLedger).toBe("CGST Input");
    expect(company.roundOffLedger).toBe("Round Off");
    expect(company.salesLedger).toBe("Sales");
    expect(company.cessLedger).toBe("");
  });

  it("sends reviewer-confirmed bank mapping without a derived transaction type", () => {
    const payload = toTransactionPayload({
      transactionDate: "2026-03-10",
      valueDate: "",
      description: "NEFT",
      referenceNumber: "",
      debitAmount: "0.00",
      creditAmount: "500.00",
      balance: "",
      counterpartyName: "Acme Ltd",
      counterpartyLedger: "Acme Ltd",
      counterpartyAddress: "",
      counterpartyPincode: "",
      counterpartyGstin: "27AACCD1234F1Z2",
      isInterAccountTransfer: false,
    });

    expect(payload.counterparty_name).toBe("Acme Ltd");
    expect(payload.counterparty_ledger).toBe("Acme Ltd");
    expect(payload.is_inter_account_transfer).toBe(false);
    expect(Object.keys(payload)).not.toContain("transaction_type");
  });
});
