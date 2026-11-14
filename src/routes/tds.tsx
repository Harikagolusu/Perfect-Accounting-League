import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Download, AlertTriangle, Percent, IndianRupee, UserX, Settings2, RefreshCw } from "lucide-react";
import { PageHeader, KPICard, InfoNotice, StatusBadge, KV, Field } from "@/components/pal/common";
import { DataTable, FilterSelect } from "@/components/pal/data-table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useScoped, useStore, useCompany } from "@/lib/store";
import { PERIODS, TDS_SECTIONS, tdsRates, periodMonthStart } from "@/lib/constants";
import { fmtDate, fmtINR } from "@/lib/format";
import { quarterOf, tdsDue, tdsVariance } from "@/lib/tds";
import { downloadBlob } from "@/lib/export";
import { tallyApi } from "@/lib/api/tally";
import { useTallyTdsWorking } from "@/lib/queries/tally";
import type { TDSSection, TDSWorking } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/tds")({
  head: () => seo("TDS Workings", "Section-wise TDS computation, shortfall alerts and missing PAN warnings."),
  component: TDSPage,
});

const MONTHS: Record<string, string> = Object.fromEntries(PERIODS.map((p, i) => [p, String(((i + 3) % 12) + 1).padStart(2, "0")]));

function TDSPage() {
  const s = useStore();
  const company = useCompany();
  const all = useScoped(s.tds);
  const tdsSync = useTallyTdsWorking();
  const [downloading, setDownloading] = useState(false);
  const [q, setQ] = useState("All");
  const [month, setMonth] = useState("All");
  const [vendor, setVendor] = useState("All");
  const [section, setSection] = useState("All");
  const [status, setStatus] = useState("All");
  const [open, setOpen] = useState<TDSWorking | null>(null);
  const [rules, setRules] = useState(false);
  const [rateDraft, setRateDraft] = useState<Record<string, number>>({ ...tdsRates });

  const rows = all.filter((t) =>
    (q === "All" || quarterOf(t.voucherDate) === q) && (month === "All" || t.voucherDate.slice(5, 7) === MONTHS[month]) &&
    (vendor === "All" || t.vendor === vendor) && (section === "All" || t.section === section) && (status === "All" || t.status === status));
  const shortfall = rows.reduce((a, t) => a + Math.max(0, tdsVariance(t)), 0);
  const missingPan = rows.filter((t) => !t.pan).length;

  const markReviewed = (id: string) => { s.setTds((x) => x.map((t) => (t.id === id ? { ...t, status: "Reviewed" } : t))); toast.success("Marked reviewed"); setOpen(null); };

  const tallyName = () => company?.legalName ?? company?.displayName ?? "";
  // Whole financial year (Apr–Mar) for the on-screen table; it filters by
  // quarter and month client-side, so one sync fills every period.
  const fyRange = () => {
    const y = Number(s.financialYear.match(/\d{4}/)?.[0]);
    return y ? { start: `${y}-04-01`, end: `${y + 1}-03-31` } : {};
  };
  // Selected tax-period month, for the downloaded workbook (the client doc is
  // one month).
  const monthRange = () => {
    const start = periodMonthStart(s.financialYear, s.period);
    if (!start) return {};
    const y = Number(start.slice(0, 4)), m = Number(start.slice(5, 7));
    return { start, end: `${start.slice(0, 7)}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}` };
  };

  // Sync the working from Tally vouchers, replacing this company's rows.
  const syncFromTally = () => {
    if (!company) return toast.error("Select a company first.");
    const name = tallyName();
    if (!name) return toast.error("This company has no name to match in Tally.");
    const { start, end } = fyRange();
    tdsSync.mutate(
      { companyId: company.id, tallyCompanyName: name, fromDate: start, toDate: end },
      {
        onSuccess: (d) => {
          if (!d.success) return toast.error(`Tally sync failed: ${d.error_message}`);
          const rows: TDSWorking[] = d.rows.map((r, i) => ({
            id: `${r.voucher_number || "v"}-${i}`,
            companyId: company.id,
            voucherDate: r.voucher_date ?? "",
            voucherNo: r.voucher_number,
            vendor: r.party_name,
            pan: r.pan,
            section: r.section as TDSSection,
            gross: Number(r.base),
            base: Number(r.base),
            // Backend rate is a fraction (tds/base); the page uses a percent.
            rate: Number(r.rate) * 100,
            deducted: Number(r.tds),
            status: r.status as TDSWorking["status"],
          }));
          s.setTds((prev) => [...prev.filter((t) => t.companyId !== company.id), ...rows]);
          toast.success(`Synced ${rows.length} voucher(s) from Tally`);
        },
        onError: (e) => toast.error(e.message),
      },
    );
  };

  // Download the month's working as .xlsx in the client's three-sheet layout,
  // built server-side from the same Tally vouchers.
  const downloadWorkbook = async () => {
    if (!company) return toast.error("Select a company first.");
    const name = tallyName();
    if (!name) return toast.error("This company has no name to match in Tally.");
    const { start, end } = monthRange();
    setDownloading(true);
    try {
      const file = await tallyApi.tdsWorkbook(company.id, name, start, end);
      downloadBlob(file.filename, file.blob);
      toast.success(`Downloaded ${file.filename}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not build the workbook.");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <>
      <PageHeader title="TDS Workings" description="Section-wise TDS review against vendor payments."
        actions={<>
          <Button variant="outline" onClick={syncFromTally} disabled={tdsSync.isPending}>
            <RefreshCw className={tdsSync.isPending ? "animate-spin" : undefined} />{tdsSync.isPending ? "Syncing…" : "Sync from Tally"}
          </Button>
          <Button variant="outline" onClick={() => setRules(true)}><Settings2 />Rule configuration</Button>
          <Button onClick={downloadWorkbook} disabled={downloading}><Download />{downloading ? "Generating…" : "Export to Excel"}</Button>
        </>} />
      <InfoNotice>Sync reads this company's TDS deductions from TallyPrime for the financial year; Export to Excel downloads the selected month in the client's three-sheet format (Non Salary filled; Salary and PL sep are templates). The section is inferred from the ledger name — reference only, not statutory advice.</InfoNotice>
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KPICard label="Base value" value={fmtINR(rows.reduce((a, t) => a + t.base, 0))} icon={IndianRupee} hint={`${rows.length} vouchers`} />
        <KPICard label="TDS due" value={fmtINR(rows.reduce((a, t) => a + tdsDue(t), 0))} icon={Percent} />
        <KPICard label="Shortfall" value={fmtINR(shortfall)} icon={AlertTriangle} tone="danger" hint="under-deducted" />
        <KPICard label="Missing PAN" value={missingPan} icon={UserX} tone="warning" hint="206AA may apply" />
      </div>
      {(shortfall > 0 || missingPan > 0) && (
        <div className="mb-4 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
          {shortfall > 0 && <div>Shortfall alert: {fmtINR(shortfall)} under-deducted across filtered vouchers.</div>}
          {missingPan > 0 && <div>{missingPan} voucher(s) have no vendor PAN — higher rate may apply.</div>}
        </div>
      )}
      <DataTable
        rows={rows}
        rowKey={(r) => r.id}
        searchKeys={(r) => `${r.vendor} ${r.voucherNo} ${r.pan}`}
        onRowClick={setOpen}
        toolbar={<>
          <FilterSelect label="Quarter" value={q} onChange={setQ} options={["Q1", "Q2", "Q3", "Q4"]} />
          <FilterSelect label="Month" value={month} onChange={setMonth} options={PERIODS} />
          <FilterSelect label="Vendor" value={vendor} onChange={setVendor} options={Array.from(new Set(all.map((t) => t.vendor)))} />
          <FilterSelect label="Section" value={section} onChange={setSection} options={TDS_SECTIONS} />
          <FilterSelect label="Status" value={status} onChange={setStatus} options={["Reviewed", "Pending", "Exception"]} />
        </>}
        columns={[
          { key: "voucherDate", header: "Date", cell: (r) => fmtDate(r.voucherDate) },
          { key: "voucherNo", header: "Voucher No." },
          { key: "vendor", header: "Vendor" },
          { key: "pan", header: "PAN", cell: (r) => (r.pan ? <span className="num">{r.pan}</span> : <span className="text-xs font-medium text-destructive">Missing</span>) },
          { key: "section", header: "Section" },
          { key: "gross", header: "Gross", align: "right", sortValue: (r) => r.gross, cell: (r) => fmtINR(r.gross) },
          { key: "base", header: "Base", align: "right", sortValue: (r) => r.base, cell: (r) => fmtINR(r.base) },
          { key: "rate", header: "Rate", align: "right", cell: (r) => `${r.rate}%` },
          { key: "due", header: "TDS Due", align: "right", sortValue: tdsDue, cell: (r) => fmtINR(tdsDue(r)) },
          { key: "deducted", header: "Deducted", align: "right", sortValue: (r) => r.deducted, cell: (r) => fmtINR(r.deducted) },
          { key: "variance", header: "Variance", align: "right", sortValue: tdsVariance, cell: (r) => <span className={tdsVariance(r) ? "text-destructive" : ""}>{fmtINR(tdsVariance(r))}</span> },
          { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
        ]}
      />
      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full sm:max-w-md">
          <SheetHeader><SheetTitle>{open?.voucherNo} · {open?.vendor}</SheetTitle></SheetHeader>
          {open && (
            <div className="mt-4 space-y-4">
              <div>
                <KV k="Section" v={open.section} /><KV k="PAN" v={open.pan || "Missing"} /><KV k="Base value" v={fmtINR(open.base)} />
                <KV k="Rate" v={`${open.rate}%`} /><KV k="Calculation" v={`${fmtINR(open.base)} × ${open.rate}% = ${fmtINR(tdsDue(open))}`} />
                <KV k="Deducted" v={fmtINR(open.deducted)} /><KV k="Variance" v={fmtINR(tdsVariance(open))} /><KV k="Status" v={<StatusBadge status={open.status} />} />
              </div>
              <Button className="w-full" onClick={() => markReviewed(open.id)}>Mark calculation reviewed</Button>
            </div>
          )}
        </SheetContent>
      </Sheet>
      <Dialog open={rules} onOpenChange={setRules}>
        <DialogContent>
          <DialogHeader><DialogTitle>TDS rule configuration</DialogTitle></DialogHeader>
          <div className="grid gap-3">
            {TDS_SECTIONS.map((sec) => (
              <Field key={sec} label={`Section ${sec} rate (%)`}><Input type="number" step="0.1" className="num" value={rateDraft[sec]} onChange={(e) => setRateDraft({ ...rateDraft, [sec]: Number(e.target.value) })} /></Field>
            ))}
            <p className="text-xs text-muted-foreground">Rules will be stored and enforced by the backend. Changes here are not applied to vouchers.</p>
          </div>
          <DialogFooter><Button onClick={() => { setRules(false); toast.success("Rule draft saved for backend configuration"); }}>Save draft</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
