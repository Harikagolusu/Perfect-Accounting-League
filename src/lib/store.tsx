// Session state shared by the app. Domain records come from the API; the
// remaining arrays are local-only placeholders used by modules whose backend
// does not exist yet.
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { FINANCIAL_YEARS, currentPeriod } from "./constants";
import { useCompanies } from "./queries/companies";
import type {
  AuditEvent,
  BankTransaction,
  Company,
  ExportJob,
  InboxDocument,
  LedgerMapping,
  ReconciliationRecord,
  ReviewTask,
  TDSWorking,
  Vendor,
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
  // Companies are API-backed and read-only here: writes go through the
  // companies API and React Query invalidation refills the list.
  companies: Company[];
  vendors: Vendor[];
  setVendors: Setter<Vendor>;
  mappings: LedgerMapping[];
  setMappings: Setter<LedgerMapping>;
  documents: InboxDocument[];
  setDocuments: Setter<InboxDocument>;
  bankTx: BankTransaction[];
  setBankTx: Setter<BankTransaction>;
  tds: TDSWorking[];
  setTds: Setter<TDSWorking>;
  reco: ReconciliationRecord[];
  setReco: Setter<ReconciliationRecord>;
  reviews: ReviewTask[];
  setReviews: Setter<ReviewTask>;
  audit: AuditEvent[];
  exports: ExportJob[];
  log: (e: Omit<AuditEvent, "id" | "timestamp" | "user" | "companyId" | "status">) => void;
  addExport: (e: Omit<ExportJob, "id" | "companyId" | "generatedAt">) => void;
}

const Ctx = createContext<Store | null>(null);
const PREFS_KEY = "pal.prefs"; // non-sensitive UI preferences only

export function StoreProvider({ children }: { children: ReactNode }) {
  const [companyId, setCompanyId] = useState("");
  const [financialYear, setFinancialYear] = useState(FINANCIAL_YEARS[0]);
  const [period, setPeriod] = useState(currentPeriod());
  const [role, setRole] = useState<Store["role"]>("Partner");

  const { data: apiCompanies } = useCompanies(true);
  const companies = useMemo(() => apiCompanies ?? [], [apiCompanies]);

  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [mappings, setMappings] = useState<LedgerMapping[]>([]);
  const [documents, setDocuments] = useState<InboxDocument[]>([]);
  const [bankTx, setBankTx] = useState<BankTransaction[]>([]);
  const [tds, setTds] = useState<TDSWorking[]>([]);
  const [reco, setReco] = useState<ReconciliationRecord[]>([]);
  const [reviews, setReviews] = useState<ReviewTask[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [exports, setExports] = useState<ExportJob[]>([]);

  useEffect(() => {
    try {
      const p = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
      if (p.companyId) setCompanyId(p.companyId);
      if (p.financialYear) setFinancialYear(p.financialYear);
      if (p.period) setPeriod(p.period);
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ companyId, financialYear, period }));
  }, [companyId, financialYear, period]);

  // The selected company must be a real backend UUID, because company-scoped
  // requests address /companies/{company_id}/... . Pick the first real company
  // when nothing valid is selected.
  useEffect(() => {
    if (!companies.length) return;
    if (companyId && companies.some((c) => c.id === companyId)) return;
    setCompanyId(companies[0].id);
  }, [companies, companyId]);

  const value = useMemo<Store>(() => {
    const now = () => new Date().toISOString().slice(0, 16).replace("T", " ");
    return {
      companyId,
      setCompanyId,
      financialYear,
      setFinancialYear,
      period,
      setPeriod,
      role,
      setRole,
      companies,
      vendors,
      setVendors,
      mappings,
      setMappings,
      documents,
      setDocuments,
      bankTx,
      setBankTx,
      tds,
      setTds,
      reco,
      setReco,
      reviews,
      setReviews,
      audit,
      exports,
      log: (e) =>
        setAudit((a) => [
          {
            ...e,
            id: `AE-${Date.now()}`,
            timestamp: now(),
            user: "Local user",
            companyId,
            status: "Success",
          },
          ...a,
        ]),
      addExport: (e) =>
        setExports((x) => [
          {
            ...e,
            id: `EX-${Date.now().toString().slice(-5)}`,
            companyId,
            generatedAt: now().slice(0, 10),
          },
          ...x,
        ]),
    };
  }, [
    companyId,
    setCompanyId,
    financialYear,
    period,
    role,
    companies,
    vendors,
    mappings,
    documents,
    bankTx,
    tds,
    reco,
    reviews,
    audit,
    exports,
  ]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore() {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore must be used inside StoreProvider");
  return s;
}

/** Returns only rows belonging to the selected company. UI scoping only - the backend must enforce isolation. */
export function useScoped<T extends { companyId: string }>(rows: T[]) {
  const { companyId } = useStore();
  return useMemo(() => rows.filter((r) => r.companyId === companyId), [rows, companyId]);
}

export function useCompany() {
  const { companies, companyId } = useStore();
  return companies.find((c) => c.id === companyId) ?? companies[0];
}
