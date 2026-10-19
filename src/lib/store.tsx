// Frontend-only demo state. This is the seam where API calls will plug in:
// replace the initial mock arrays with fetched data and the setters with mutations.
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import * as mock from "./mock-data";
import type {
  AuditEvent, BankTransaction, Company, ExportJob, InboxDocument, LedgerMapping,
  ReconciliationRecord, ReviewTask, TDSWorking, Vendor,
} from "./types";

type Setter<T> = React.Dispatch<React.SetStateAction<T[]>>;

interface Store {
  companyId: string;
  setCompanyId: (id: string) => void;
  financialYear: string;
  setFinancialYear: (fy: string) => void;
  period: string;
  setPeriod: (p: string) => void;
  role: "Partner" | "Accountant" | "Viewer";
  setRole: (r: Store["role"]) => void;
  companies: Company[]; setCompanies: Setter<Company>;
  vendors: Vendor[]; setVendors: Setter<Vendor>;
  mappings: LedgerMapping[]; setMappings: Setter<LedgerMapping>;
  documents: InboxDocument[]; setDocuments: Setter<InboxDocument>;
  bankTx: BankTransaction[]; setBankTx: Setter<BankTransaction>;
  tds: TDSWorking[]; setTds: Setter<TDSWorking>;
  reco: ReconciliationRecord[]; setReco: Setter<ReconciliationRecord>;
  reviews: ReviewTask[]; setReviews: Setter<ReviewTask>;
  audit: AuditEvent[];
  exports: ExportJob[];
  log: (e: Omit<AuditEvent, "id" | "timestamp" | "user" | "companyId" | "status">) => void;
  addExport: (e: Omit<ExportJob, "id" | "companyId" | "generatedAt">) => void;
}

const Ctx = createContext<Store | null>(null);
const PREFS_KEY = "pal.prefs"; // non-sensitive UI preferences only

export function StoreProvider({ children }: { children: ReactNode }) {
  const [companyId, setCompanyId] = useState("c1");
  const [financialYear, setFinancialYear] = useState(mock.FINANCIAL_YEARS[0]);
  const [period, setPeriod] = useState("October");
  const [role, setRole] = useState<Store["role"]>("Partner");
  const [companies, setCompanies] = useState(mock.companies);
  const [vendors, setVendors] = useState(mock.vendors);
  const [mappings, setMappings] = useState(mock.ledgerMappings);
  const [documents, setDocuments] = useState(mock.documents);
  const [bankTx, setBankTx] = useState(mock.bankTransactions);
  const [tds, setTds] = useState(mock.tdsWorkings);
  const [reco, setReco] = useState(mock.reconciliationRecords);
  const [reviews, setReviews] = useState(mock.reviewTasks);
  const [audit, setAudit] = useState(mock.auditEvents);
  const [exports, setExports] = useState(mock.exportJobs);

  useEffect(() => {
    try {
      const p = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
      if (p.companyId) setCompanyId(p.companyId);
      if (p.financialYear) setFinancialYear(p.financialYear);
      if (p.period) setPeriod(p.period);
    } catch { /* ignore */ }
  }, []);
  useEffect(() => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ companyId, financialYear, period }));
  }, [companyId, financialYear, period]);

  const value = useMemo<Store>(() => {
    const now = () => new Date().toISOString().slice(0, 16).replace("T", " ");
    return {
      companyId, setCompanyId, financialYear, setFinancialYear, period, setPeriod, role, setRole,
      companies, setCompanies, vendors, setVendors, mappings, setMappings, documents, setDocuments,
      bankTx, setBankTx, tds, setTds, reco, setReco, reviews, setReviews, audit, exports,
      log: (e) =>
        setAudit((a) => [
          { ...e, id: `AE-${Date.now()}`, timestamp: now(), user: "Ananya Rao", companyId, status: "Success" },
          ...a,
        ]),
      addExport: (e) =>
        setExports((x) => [
          { ...e, id: `EX-${Date.now().toString().slice(-5)}`, companyId, generatedAt: now().slice(0, 10) },
          ...x,
        ]),
    };
  }, [companyId, financialYear, period, role, companies, vendors, mappings, documents, bankTx, tds, reco, reviews, audit, exports]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore() {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore must be used inside StoreProvider");
  return s;
}

/** Returns only rows belonging to the selected company. UI scoping only — the backend must enforce isolation. */
export function useScoped<T extends { companyId: string }>(rows: T[]) {
  const { companyId } = useStore();
  return useMemo(() => rows.filter((r) => r.companyId === companyId), [rows, companyId]);
}

export function useCompany() {
  const { companies, companyId } = useStore();
  return companies.find((c) => c.id === companyId) ?? companies[0];
}
