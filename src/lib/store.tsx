// Session state shared by the app.
//
// Domain records come from the API (see src/lib/queries/*): companies, vendors,
// invoices, documents, bank data, vouchers, ledgers, mappings and the audit
// trail are all persisted in PostgreSQL and read through React Query, so they
// survive a reload, a restart and reopening PAL tomorrow.
//
// The arrays that remain here (tds, reco, reviews, exports) belong to modules
// whose backend does not exist yet. They are local-only placeholders: data in
// them lives for the browser session only. Only non-sensitive UI preferences
// (selected company / financial year / period) are persisted, to localStorage.
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { FINANCIAL_YEARS, currentPeriod } from "./constants";
import { useCompanies } from "./queries/companies";
import type { Company, ExportJob, ReconciliationRecord, ReviewTask, TDSWorking } from "./types";

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
  tds: TDSWorking[];
  setTds: Setter<TDSWorking>;
  reco: ReconciliationRecord[];
  setReco: Setter<ReconciliationRecord>;
  reviews: ReviewTask[];
  setReviews: Setter<ReviewTask>;
  exports: ExportJob[];
  addExport: (e: Omit<ExportJob, "id" | "companyId" | "generatedAt">) => void;
}

const Ctx = createContext<Store | null>(null);
const PREFS_KEY = "pal.prefs"; // non-sensitive UI preferences only

export function StoreProvider({ children }: { children: ReactNode }) {
  const [companyId, setCompanyId] = useState("");
  const [financialYear, setFinancialYear] = useState(FINANCIAL_YEARS[0]);
  const [period, setPeriod] = useState(currentPeriod());
  const [role, setRole] = useState<Store["role"]>("Partner");

  // The header selector is for active work. Archived (INACTIVE) companies stay
  // in the Companies page for audit/history, but must not remain selectable.
  const { data: apiCompanies } = useCompanies(false);
  const companies = useMemo(() => apiCompanies ?? [], [apiCompanies]);

  const [tds, setTds] = useState<TDSWorking[]>([]);
  const [reco, setReco] = useState<ReconciliationRecord[]>([]);
  const [reviews, setReviews] = useState<ReviewTask[]>([]);
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
      tds,
      setTds,
      reco,
      setReco,
      reviews,
      setReviews,
      exports,
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
    tds,
    reco,
    reviews,
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
