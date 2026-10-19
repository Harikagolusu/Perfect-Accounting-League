// Fictional SAMPLE data for demo purposes only. Replace with API responses.
import type {
  AuditEvent,
  BankTransaction,
  Company,
  ExportJob,
  InboxDocument,
  Invoice,
  LedgerMapping,
  ReconciliationRecord,
  ReviewTask,
  TDSWorking,
  Vendor,
  DocStatus,
  RecoStatus,
  TDSSection,
  ReviewType,
} from "./types";

// Deterministic pseudo-random so SSR and client render identical data.
function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}
const pick = <T,>(r: () => number, arr: readonly T[]): T => arr[Math.floor(r() * arr.length)]!;
const day = (offset: number) => {
  const d = new Date(Date.UTC(2026, 9, 18));
  d.setUTCDate(d.getUTCDate() - offset);
  return d.toISOString().slice(0, 10);
};

export const USERS = ["Ananya Rao", "Vikram Mehta", "Priya Nair", "Rohit Sharma", "Kavya Iyer"];
export const FINANCIAL_YEARS = ["FY 2026-27", "FY 2025-26", "FY 2024-25"];
export const PERIODS = [
  "April", "May", "June", "July", "August", "September",
  "October", "November", "December", "January", "February", "March",
];
export const STATES = ["Maharashtra", "Karnataka", "Tamil Nadu", "Delhi", "Gujarat", "Telangana", "West Bengal"];
export const BANKS = ["HDFC", "ICICI", "SBI", "Axis", "Kotak", "Standard Chartered", "IndusInd", "PNB"];
export const EXPENSE_LEDGERS = [
  "Professional Fees", "Rent", "Office Supplies", "Freight Inward", "Software Subscriptions",
  "Repairs & Maintenance", "Advertising", "Travel Expenses", "Electricity", "Purchase - Raw Material", "Contract Labour",
];
export const TAX_LEDGERS = ["Input CGST", "Input SGST", "Input IGST", "Input Cess"];
export const PARENT_GROUPS = ["Indirect Expenses", "Direct Expenses", "Purchase Accounts", "Sundry Creditors"];

export const companies: Company[] = [
  {
    id: "c1", legalName: "Sahyadri Agro Foods Private Limited", displayName: "Sahyadri Agro",
    pan: "AAJCS4821K", gstin: "27AAJCS4821K1Z5", address: "Plot 14, MIDC Bhosari, Pune 411026",
    state: "Maharashtra", contactPerson: "Meera Kulkarni", email: "accounts@sahyadri-demo.in",
    phone: "+91 98220 11223", financialYear: "FY 2026-27", software: "Tally Prime",
    assignedUsers: ["Ananya Rao", "Vikram Mehta"], status: "Active",
  },
  {
    id: "c2", legalName: "Nilgiri Tech Solutions LLP", displayName: "Nilgiri Tech",
    pan: "AARFN7310M", gstin: "29AARFN7310M1Z2", address: "4th Floor, Prestige Tower, Bengaluru 560001",
    state: "Karnataka", contactPerson: "Arjun Reddy", email: "finance@nilgiri-demo.in",
    phone: "+91 99001 22334", financialYear: "FY 2026-27", software: "Zoho Books",
    assignedUsers: ["Priya Nair"], status: "Active",
  },
  {
    id: "c3", legalName: "Coromandel Logistics Private Limited", displayName: "Coromandel Logistics",
    pan: "AAECC9156P", gstin: "33AAECC9156P1Z8", address: "22 Harbour Road, Chennai 600001",
    state: "Tamil Nadu", contactPerson: "S. Venkatesh", email: "ap@coromandel-demo.in",
    phone: "+91 94440 33445", financialYear: "FY 2026-27", software: "Tally Prime",
    assignedUsers: ["Rohit Sharma", "Kavya Iyer"], status: "Active",
  },
  {
    id: "c4", legalName: "Yamuna Retail Ventures Private Limited", displayName: "Yamuna Retail",
    pan: "AABCY2204Q", gstin: "07AABCY2204Q1Z3", address: "B-12 Connaught Place, New Delhi 110001",
    state: "Delhi", contactPerson: "Neha Bansal", email: "books@yamuna-demo.in",
    phone: "+91 98110 44556", financialYear: "FY 2026-27", software: "None",
    assignedUsers: ["Ananya Rao"], status: "Onboarding",
  },
];

const VENDOR_NAMES = [
  "Kaveri Packaging Industries", "Deccan Freight Carriers", "Shree Ganesh Stationers", "Bluepeak Cloud Services",
  "Mehta & Associates Chartered Accountants", "Urban Spaces Realty", "Sunrise Electricals", "Prism Advertising Co",
  "Konkan Cold Chain", "Vertex Facility Services", "Indigo Travels", "Apex Steel Traders",
];
const STATE_CODES: Record<string, string> = { c1: "27", c2: "29", c3: "33", c4: "07" };

function fakePan(r: () => number, type = "C") {
  const L = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const l = () => L[Math.floor(r() * L.length)];
  return `AA${l()}${type}${l()}${Math.floor(1000 + r() * 8999)}${l()}`;
}

export const vendors: Vendor[] = companies.flatMap((c, ci) => {
  const r = rng(100 + ci);
  return VENDOR_NAMES.slice(0, 9 + ci).map((name, i) => {
    const pan = fakePan(r, i === 4 ? "F" : "C");
    return {
      id: `v-${c.id}-${i}`,
      companyId: c.id,
      name,
      pan,
      gstin: `${STATE_CODES[c.id]}${pan}1Z${Math.floor(r() * 9)}`,
      defaultExpenseLedger: EXPENSE_LEDGERS[(i + ci) % EXPENSE_LEDGERS.length],
      creditorLedger: name,
      parentGroup: i % 4 === 3 ? "Direct Expenses" : "Indirect Expenses",
      transactions: Math.floor(4 + r() * 80),
      lastUsed: day(Math.floor(r() * 60)),
      status: i === 7 ? "Needs Mapping" : i === 8 ? "Inactive" : "Active",
    } as Vendor;
  });
});

export const ledgerMappings: LedgerMapping[] = vendors.map((v, i) => {
  const r = rng(300 + i);
  const cat = i % 5 === 3 ? "Description/Keyword Match" : i % 7 === 6 ? "Manual Review Required" : "Exact GSTIN/PAN Match";
  const conf = cat === "Exact GSTIN/PAN Match" ? 90 + Math.floor(r() * 9) : cat === "Description/Keyword Match" ? 72 + Math.floor(r() * 15) : 40 + Math.floor(r() * 25);
  return {
    id: `m-${v.id}`,
    companyId: v.companyId,
    vendorName: v.name,
    vendorKey: v.gstin,
    keywords: v.defaultExpenseLedger.toLowerCase().split(" ").slice(0, 2).join(", "),
    expenseLedger: v.defaultExpenseLedger,
    creditorLedger: v.creditorLedger,
    taxLedger: i % 3 === 0 ? "Input IGST" : "Input CGST / SGST",
    confidence: conf,
    source: cat,
    approvedBy: cat === "Manual Review Required" ? "—" : pick(r, USERS),
    updatedAt: day(Math.floor(r() * 90)),
    status: cat === "Manual Review Required" ? "Pending" : "Approved",
  };
});

const DOC_STATUSES: DocStatus[] = ["Received", "Queued", "Processing", "Extracted", "Needs Review", "Approved", "Exported", "Failed"];
export const documents: InboxDocument[] = companies.flatMap((c, ci) => {
  const r = rng(500 + ci);
  return Array.from({ length: 22 }, (_, i) => {
    const vendor = pick(r, VENDOR_NAMES);
    const ext = pick(r, ["PDF", "PDF", "PDF", "JPEG", "PNG", "XLSX", "CSV"]);
    return {
      id: `D-${c.id.toUpperCase()}-${1040 + i}`,
      companyId: c.id,
      name: `${vendor.split(" ")[0]}_INV_${2600 + i}.${ext.toLowerCase()}`,
      source: r() > 0.45 ? "Email" : "Manual Upload",
      receivedAt: day(Math.floor(i / 2)),
      fileType: ext,
      status: DOC_STATUSES[Math.floor(r() * DOC_STATUSES.length)],
      reviewer: pick(r, USERS),
      size: Math.floor(80 + r() * 2400) * 1024,
    } as InboxDocument;
  });
});

export function buildInvoice(doc: InboxDocument): Invoice {
  const r = rng(doc.id.split("").reduce((a, ch) => a + ch.charCodeAt(0), 0));
  const companyVendors = vendors.filter((v) => v.companyId === doc.companyId);
  const v = pick(r, companyVendors);
  const company = companies.find((c) => c.id === doc.companyId)!;
  const inter = r() > 0.6;
  const items = Array.from({ length: 2 + Math.floor(r() * 3) }, (_, i) => ({
    id: `li-${i}`,
    description: pick(r, ["Corrugated boxes 5-ply", "Freight charges - Pune to Mumbai", "Annual software licence", "Consulting services", "A4 copier paper", "Electrical fittings", "Cold storage charges"]),
    hsn: pick(r, ["4819", "996511", "997331", "998311", "4802", "8536", "996721"]),
    qty: 1 + Math.floor(r() * 50),
    unit: pick(r, ["NOS", "KGS", "HRS", "BOX"]),
    unitPrice: Math.round(200 + r() * 5000),
    taxRate: pick(r, [5, 12, 18, 18, 28]),
  }));
  const tax = items.reduce((s, li) => s + (li.qty * li.unitPrice * li.taxRate) / 100, 0);
  const conf = Math.floor(55 + r() * 44);
  return {
    id: `INV-${doc.id}`,
    companyId: doc.companyId,
    documentId: doc.id,
    vendorName: v.name,
    invoiceNumber: `${v.name.slice(0, 3).toUpperCase()}/26-27/${Math.floor(100 + r() * 900)}`,
    invoiceDate: doc.receivedAt,
    vendorGstin: v.gstin,
    vendorPan: v.pan,
    buyerGstin: company.gstin,
    poNumber: `PO-${Math.floor(4000 + r() * 999)}`,
    cgst: inter ? 0 : Math.round(tax / 2),
    sgst: inter ? 0 : Math.round(tax / 2),
    igst: inter ? Math.round(tax) : 0,
    cess: 0,
    roundOff: 0,
    lineItems: items,
    mapping: {
      expenseLedger: v.defaultExpenseLedger,
      vendorLedger: v.creditorLedger,
      parentGroup: v.parentGroup,
      taxLedger: inter ? "Input IGST" : "Input CGST / SGST",
      source: conf >= 85 ? "Exact GSTIN/PAN Match" : conf >= 70 ? "Description/Keyword Match" : "Manual Review Required",
      confidence: conf,
      status: "Suggested",
    },
    status: conf < 80 ? "Needs Review" : "Draft",
  };
}

const NARRATIONS = [
  ["NEFT DR-HDFC0001234-KAVERI PACKAGING IND-NETBANK, MUM-N2026101", "Kaveri Packaging Industries", "Purchase - Raw Material"],
  ["UPI/DR/628812/DECCAN FREIGHT/YESB/payment", "Deccan Freight Carriers", "Freight Inward"],
  ["IMPS-P2A-662019-URBAN SPACES REALTY-RENT OCT", "Urban Spaces Realty", "Rent"],
  ["ACH D- BLUEPEAK CLOUD-SUBSCR 0045", "Bluepeak Cloud Services", "Software Subscriptions"],
  ["NEFT CR-ICIC0000045-RELIANCE RETAIL LTD-INV 9921", "Reliance Retail Ltd", "Sundry Debtors"],
  ["CHQ DEP 004512 CLG", "", ""],
  ["MSEDCL ELECTRICITY BILL BBPS 99812", "MSEDCL", "Electricity"],
  ["RTGS CR-SBIN0004433-METRO CASH AND CARRY", "Metro Cash and Carry", "Sundry Debtors"],
  ["POS 4521XXXX INDIGO AIRLINES", "Indigo Travels", "Travel Expenses"],
  ["BANK CHARGES GST INCL", "HDFC Bank", "Bank Charges"],
] as const;

export const bankTransactions: BankTransaction[] = companies.flatMap((c, ci) => {
  const r = rng(700 + ci);
  let bal = 1250000 + ci * 300000;
  return Array.from({ length: 28 }, (_, i) => {
    const n = NARRATIONS[Math.floor(r() * NARRATIONS.length)];
    const isCredit = n[2] === "Sundry Debtors" || n[0].startsWith("CHQ DEP");
    const amt = Math.round(2000 + r() * 180000);
    bal += isCredit ? amt : -amt;
    const conf = n[1] ? Math.floor(62 + r() * 37) : 0;
    return {
      id: `BT-${c.id}-${i}`,
      companyId: c.id,
      date: day(30 - i),
      valueDate: day(30 - i),
      rawNarration: n[0],
      cleanNarration: n[1] ? `${isCredit ? "Receipt from" : "Payment to"} ${n[1]}` : "Cheque deposit — counterparty unknown",
      reference: `${Math.floor(100000000 + r() * 899999999)}`,
      debit: isCredit ? 0 : amt,
      credit: isCredit ? amt : 0,
      balance: bal,
      counterparty: n[1],
      ledger: n[2],
      confidence: conf,
      status: !n[1] ? "Unmapped" : conf >= 90 ? "Approved" : "Suggested",
    } as BankTransaction;
  });
});

const SECTION_RATE: Record<TDSSection, number> = { "194C": 2, "194J": 10, "194I": 10, "194H": 5, "194Q": 0.1 };
export const TDS_SECTIONS = Object.keys(SECTION_RATE) as TDSSection[];
export const tdsRates = SECTION_RATE;

export const tdsWorkings: TDSWorking[] = companies.flatMap((c, ci) => {
  const r = rng(900 + ci);
  const cv = vendors.filter((v) => v.companyId === c.id);
  return Array.from({ length: 24 }, (_, i) => {
    const v = pick(r, cv);
    const section = pick(r, TDS_SECTIONS);
    const base = Math.round(15000 + r() * 400000);
    const rate = SECTION_RATE[section];
    const due = Math.round((base * rate) / 100);
    const roll = r();
    const deducted = roll > 0.8 ? Math.round(due * 0.5) : roll > 0.72 ? 0 : due;
    const pan = i % 11 === 5 ? "" : v.pan;
    return {
      id: `TDS-${c.id}-${i}`,
      companyId: c.id,
      voucherDate: day(Math.floor(r() * 170)),
      voucherNo: `JV/${1200 + i}`,
      vendor: v.name,
      pan,
      section,
      gross: Math.round(base * 1.18),
      base,
      rate,
      deducted,
      status: deducted !== due || !pan ? "Exception" : r() > 0.5 ? "Reviewed" : "Pending",
    } as TDSWorking;
  });
});

const RECO: RecoStatus[] = [
  "Exact Match", "Exact Match", "Exact Match", "Exact Match", "Probable Match", "Value Mismatch",
  "In Books, Missing in 2B", "In 2B, Missing in Books", "Tax Head Mismatch", "Needs Review",
];
export const reconciliationRecords: ReconciliationRecord[] = companies.flatMap((c, ci) => {
  const r = rng(1100 + ci);
  const cv = vendors.filter((v) => v.companyId === c.id);
  return Array.from({ length: 36 }, (_, i) => {
    const v = pick(r, cv);
    const status = pick(r, RECO);
    const taxable = Math.round(10000 + r() * 500000);
    const gst = Math.round(taxable * 0.18);
    const diff = status === "Value Mismatch" ? Math.round(500 + r() * 9000) : 0;
    return {
      id: `R-${c.id}-${i}`,
      companyId: c.id,
      supplier: v.name,
      gstin: v.gstin,
      invoiceNo: `${v.name.slice(0, 3).toUpperCase()}/${2600 + i}`,
      invoiceDate: day(Math.floor(r() * 30) + 20),
      booksTaxable: status === "In 2B, Missing in Books" ? null : taxable,
      twoBTaxable: status === "In Books, Missing in 2B" ? null : taxable - diff,
      booksGst: status === "In 2B, Missing in Books" ? null : gst,
      twoBGst: status === "In Books, Missing in 2B" ? null : Math.round((taxable - diff) * 0.18),
      status,
    };
  });
});

const REVIEW_TYPES: ReviewType[] = [
  "Invoice Data Review", "Ledger Mapping Review", "Low Confidence Extraction",
  "Bank Transaction Review", "GST Mismatch Review", "TDS Exception Review",
];
const REVIEW_DESC: Record<ReviewType, string> = {
  "Invoice Data Review": "Verify extracted totals against invoice image",
  "Ledger Mapping Review": "New vendor — confirm expense ledger",
  "Low Confidence Extraction": "OCR confidence below threshold on GSTIN",
  "Bank Transaction Review": "Unidentified cheque deposit",
  "GST Mismatch Review": "Taxable value differs from GSTR-2B",
  "TDS Exception Review": "TDS shortfall under section 194C",
};
export const reviewTasks: ReviewTask[] = companies.flatMap((c, ci) => {
  const r = rng(1300 + ci);
  return Array.from({ length: 14 }, (_, i) => {
    const type = pick(r, REVIEW_TYPES);
    return {
      id: `RV-${c.id.toUpperCase()}-${300 + i}`,
      companyId: c.id,
      type,
      description: REVIEW_DESC[type],
      assignedTo: pick(r, USERS),
      createdAt: day(Math.floor(r() * 14)),
      priority: pick(r, ["High", "Medium", "Low"] as const),
      status: i > 11 ? "Approved" : "Open",
      comments: [],
    } as ReviewTask;
  });
});

const MODULES = ["Invoice Processing", "Vendor Master", "Bank Statements", "GST Reconciliation", "TDS Workings", "Settings", "Reports"];
const ACTIONS = ["Created", "Updated", "Approved", "Rejected", "Exported", "Deleted"];
export const auditEvents: AuditEvent[] = companies.flatMap((c, ci) => {
  const r = rng(1500 + ci);
  return Array.from({ length: 30 }, (_, i) => {
    const mod = pick(r, MODULES);
    const action = pick(r, ACTIONS);
    const h = String(Math.floor(9 + r() * 9)).padStart(2, "0");
    const m = String(Math.floor(r() * 60)).padStart(2, "0");
    return {
      id: `AE-${c.id}-${i}`,
      timestamp: `${day(Math.floor(i / 3))} ${h}:${m}`,
      user: pick(r, USERS),
      companyId: c.id,
      module: mod,
      action,
      recordId: `${mod.slice(0, 3).toUpperCase()}-${1000 + Math.floor(r() * 9000)}`,
      previous: action === "Created" ? "—" : pick(r, ["Status: Pending", "Ledger: Office Supplies", "Amount: ₹12,400", "Rate: 1%"]),
      next: action === "Deleted" ? "—" : pick(r, ["Status: Approved", "Ledger: Professional Fees", "Amount: ₹12,800", "Rate: 2%"]),
      status: r() > 0.93 ? "Failed" : "Success",
    } as AuditEvent;
  });
});

export const exportJobs: ExportJob[] = companies.flatMap((c, ci) => {
  const r = rng(1700 + ci);
  return Array.from({ length: 6 }, (_, i) => ({
    id: `EX-${c.id.toUpperCase()}-${80 + i}`,
    companyId: c.id,
    name: pick(r, ["Purchase vouchers", "Vendor master", "Bank receipts", "GST working", "TDS working"]),
    type: pick(r, ["Purchase Vouchers", "Ledger Masters", "Bills", "Payments"]),
    format: "CSV",
    records: Math.floor(10 + r() * 400),
    generatedAt: day(i * 3 + 1),
    status: i === 4 ? "Failed" : "Generated",
    target: i % 2 === 0 ? "Tally" : i % 3 === 0 ? "Report" : "Zoho Books",
  }) as ExportJob);
});

export const complianceDeadlines = [
  { type: "GSTR-1 (October)", due: "2026-11-11", status: "Upcoming" },
  { type: "GSTR-3B (October)", due: "2026-11-20", status: "Upcoming" },
  { type: "TDS Payment (October)", due: "2026-11-07", status: "Upcoming" },
  { type: "TDS Return Q2 (24Q/26Q)", due: "2026-10-31", status: "Due soon" },
  { type: "GSTR-3B (September)", due: "2026-10-20", status: "Due soon" },
];

export const processingActivity = Array.from({ length: 14 }, (_, i) => {
  const r = rng(2000 + i);
  return {
    date: day(13 - i).slice(5),
    processed: Math.floor(20 + r() * 60),
    pending: Math.floor(3 + r() * 15),
    failed: Math.floor(r() * 6),
  };
});
