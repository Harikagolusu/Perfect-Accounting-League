// Domain models for PAL. Shapes mirror the future backend API contracts.

export type AccountingSoftware = "Tally Prime" | "Zoho Books" | "None";
export type EntityStatus = "Active" | "Inactive" | "Onboarding";

export interface Company {
  id: string;
  legalName: string;
  displayName: string;
  pan: string;
  gstin: string;
  address: string;
  state: string;
  contactPerson: string;
  email: string;
  phone: string;
  financialYear: string;
  software: AccountingSoftware;
  assignedUsers: string[];
  status: EntityStatus;
}

export interface Vendor {
  id: string;
  companyId: string;
  name: string;
  gstin: string;
  pan: string;
  defaultExpenseLedger: string;
  creditorLedger: string;
  parentGroup: string;
  transactions: number;
  lastUsed: string;
  status: "Active" | "Inactive" | "Needs Mapping";
}

export type DocStatus =
  | "Received"
  | "Queued"
  | "Processing"
  | "Extracted"
  | "Needs Review"
  | "Approved"
  | "Exported"
  | "Failed";

export interface InboxDocument {
  id: string;
  companyId: string;
  name: string;
  source: "Email" | "Manual Upload";
  receivedAt: string;
  fileType: string;
  status: DocStatus;
  reviewer: string;
  size: number;
}

export interface InvoiceLineItem {
  id: string;
  description: string;
  hsn: string;
  qty: number;
  unit: string;
  unitPrice: number;
  taxRate: number;
}

export type MappingCategory = "Exact GSTIN/PAN Match" | "Description/Keyword Match" | "Manual Review Required";

export interface LedgerMapping {
  id: string;
  companyId: string;
  vendorName: string;
  vendorKey: string;
  keywords: string;
  expenseLedger: string;
  creditorLedger: string;
  taxLedger: string;
  confidence: number;
  source: MappingCategory;
  approvedBy: string;
  updatedAt: string;
  status: "Approved" | "Pending";
}

export interface Invoice {
  id: string;
  companyId: string;
  documentId: string;
  vendorName: string;
  invoiceNumber: string;
  invoiceDate: string;
  vendorGstin: string;
  vendorPan: string;
  buyerGstin: string;
  poNumber: string;
  cgst: number;
  sgst: number;
  igst: number;
  cess: number;
  roundOff: number;
  lineItems: InvoiceLineItem[];
  mapping: {
    expenseLedger: string;
    vendorLedger: string;
    parentGroup: string;
    taxLedger: string;
    source: MappingCategory;
    confidence: number;
    status: "Suggested" | "Approved";
  };
  status: "Draft" | "Needs Review" | "Approved" | "Rejected" | "Exported";
}

export interface BankStatement {
  id: string;
  companyId: string;
  bank: string;
  account: string;
  from: string;
  to: string;
}

export interface BankTransaction {
  id: string;
  companyId: string;
  date: string;
  valueDate: string;
  rawNarration: string;
  cleanNarration: string;
  reference: string;
  debit: number;
  credit: number;
  balance: number;
  counterparty: string;
  ledger: string;
  confidence: number;
  status: "Approved" | "Suggested" | "Unmapped";
}

export interface GSTWorking {
  companyId: string;
  period: string;
  outward: { category: string; taxable: number; igst: number; cgst: number; sgst: number }[];
  inward: { category: string; taxable: number; igst: number; cgst: number; sgst: number }[];
}

export type TDSSection = "194C" | "194J" | "194I" | "194H" | "194Q";

export interface TDSWorking {
  id: string;
  companyId: string;
  voucherDate: string;
  voucherNo: string;
  vendor: string;
  pan: string;
  section: TDSSection;
  gross: number;
  base: number;
  rate: number;
  deducted: number;
  status: "Reviewed" | "Pending" | "Exception";
}

export type RecoStatus =
  | "Exact Match"
  | "Probable Match"
  | "Value Mismatch"
  | "In Books, Missing in 2B"
  | "In 2B, Missing in Books"
  | "Tax Head Mismatch"
  | "Needs Review";

export interface ReconciliationRecord {
  id: string;
  companyId: string;
  supplier: string;
  gstin: string;
  invoiceNo: string;
  invoiceDate: string;
  booksTaxable: number | null;
  twoBTaxable: number | null;
  booksGst: number | null;
  twoBGst: number | null;
  status: RecoStatus;
  note?: string;
}

export type ReviewType =
  | "Invoice Data Review"
  | "Ledger Mapping Review"
  | "Low Confidence Extraction"
  | "Bank Transaction Review"
  | "GST Mismatch Review"
  | "TDS Exception Review";

export interface ReviewTask {
  id: string;
  companyId: string;
  type: ReviewType;
  description: string;
  assignedTo: string;
  createdAt: string;
  priority: "High" | "Medium" | "Low";
  status: "Open" | "Approved" | "Rejected" | "Correction Requested";
  comments: string[];
}

export interface AuditEvent {
  id: string;
  timestamp: string;
  user: string;
  companyId: string;
  module: string;
  action: string;
  recordId: string;
  previous: string;
  next: string;
  status: "Success" | "Failed";
}

export interface ExportJob {
  id: string;
  companyId: string;
  name: string;
  type: string;
  format: "CSV" | "XML" | "XLSX";
  records: number;
  generatedAt: string;
  status: "Generated" | "Failed" | "Not Available";
  target: "Tally" | "Zoho Books" | "Report";
}
