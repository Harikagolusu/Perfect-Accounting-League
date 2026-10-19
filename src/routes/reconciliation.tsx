import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Download, Check, Flag, MessageSquare, Eye, CheckCircle2, Mail } from "lucide-react";
import { PageHeader, KPICard, DemoNotice, StatusBadge, Field, Section } from "@/components/pal/common";
import { DataTable } from "@/components/pal/data-table";
import { FileUploadDropzone } from "@/components/pal/dropzone";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useCompany, useScoped, useStore } from "@/lib/store";
import { FINANCIAL_YEARS, PERIODS } from "@/lib/mock-data";
import { fmtDate, fmtINR } from "@/lib/format";
import { exportSheets } from "@/lib/export";
import type { ReconciliationRecord, RecoStatus } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";
import { IndianRupee, Scale, Clock, AlertTriangle } from "lucide-react";

export const Route = createFileRoute("/reconciliation")({
  head: () => seo("GST Reconciliation", "Compare purchase register records with GSTR-2B and review discrepancies."),
  component: RecoPage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";
const TABS: { v: string; label: string; match?: RecoStatus[] }[] = [
  { v: "overview", label: "Overview" },
  { v: "exact", label: "Exact Matches", match: ["Exact Match"] },
  { v: "probable", label: "Probable Matches", match: ["Probable Match"] },
  { v: "value", label: "Value Mismatches", match: ["Value Mismatch", "Tax Head Mismatch"] },
  { v: "books", label: "In Books, Missing in 2B", match: ["In Books, Missing in 2B"] },
  { v: "2b", label: "In 2B, Missing in Books", match: ["In 2B, Missing in Books"] },
  { v: "vendor", label: "Vendor Communication", match: ["In Books, Missing in 2B", "Value Mismatch"] },
];
const diff = (r: ReconciliationRecord) => (r.booksGst ?? 0) - (r.twoBGst ?? 0);

function RecoPage() {
  const s = useStore();
  const company = useCompany();
  const recs = useScoped(s.reco);
  const [tab, setTab] = useState("overview");
  const [pr, setPr] = useState<File | null>(null);
  const [twoB, setTwoB] = useState<File | null>(null);
  const [tol, setTol] = useState({ tax: 10, days: 3, normalize: true });
  const [open, setOpen] = useState<ReconciliationRecord | null>(null);
  const [note, setNote] = useState("");

  const sum = (f: (r: ReconciliationRecord) => number) => recs.reduce((a, r) => a + f(r), 0);
  const itcBooks = sum((r) => r.booksGst ?? 0);
  const itc2b = sum((r) => r.twoBGst ?? 0);
  const matched = sum((r) => (r.status === "Exact Match" ? r.booksGst ?? 0 : 0));
  const deferred = sum((r) => (r.status === "In Books, Missing in 2B" ? r.booksGst ?? 0 : 0));
  const unclaimed = sum((r) => (r.status === "In 2B, Missing in Books" ? r.twoBGst ?? 0 : 0));
  const t = TABS.find((x) => x.v === tab)!;
  const rows = t.match ? recs.filter((r) => t.match!.includes(r.status)) : recs.filter((r) => r.status !== "Exact Match");

  const update = (id: string, patch: Partial<ReconciliationRecord>) => s.setReco((all) => all.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  const doExport = () => {
    const map = (r: ReconciliationRecord) => ({ Supplier: r.supplier, GSTIN: r.gstin, InvoiceNo: r.invoiceNo, Date: r.invoiceDate, BooksTaxable: r.booksTaxable, TwoBTaxable: r.twoBTaxable, BooksGST: r.booksGst, TwoBGST: r.twoBGst, Difference: diff(r), Status: r.status, Note: r.note ?? "" });
    exportSheets(`GST Reco ${company.displayName} ${s.period}`, [
      { name: "Executive Summary", rows: [{ Company: company.legalName, Period: `${s.period} ${s.financialYear}`, ITCBooks: itcBooks, ITC2B: itc2b, Matched: matched, Deferred: deferred, Unclaimed: unclaimed, NetVariance: itcBooks - itc2b }] },
      { name: "Matched Records", rows: recs.filter((r) => r.status === "Exact Match").map(map) },
      { name: "Discrepancies", rows: recs.filter((r) => r.status !== "Exact Match").map(map) },
      { name: "Vendor Communication", rows: recs.filter((r) => ["In Books, Missing in 2B", "Value Mismatch"].includes(r.status)).map(map) },
    ]);
    toast.success("4 report sheets downloaded as CSV");
  };

  return (
    <>
      <PageHeader title="GST Reconciliation" description="Compare purchase register records with GSTR-2B and review discrepancies." actions={<Button onClick={doExport}><Download />Export Report</Button>} />
      <DemoNotice>Sample reconciliation data. PAL does not access the GST portal; GSTR-2B must be uploaded.</DemoNotice>

      <div className="mb-4 grid gap-4 lg:grid-cols-3">
        <Section title="Reconciliation Setup" className="lg:col-span-2">
          <div className="mb-3 grid gap-3 sm:grid-cols-3">
            <Field label="Company"><select className={selectCls} value={s.companyId} onChange={(e) => s.setCompanyId(e.target.value)}>{s.companies.map((c) => <option key={c.id} value={c.id}>{c.displayName}</option>)}</select></Field>
            <Field label="Financial Year"><select className={selectCls} value={s.financialYear} onChange={(e) => s.setFinancialYear(e.target.value)}>{FINANCIAL_YEARS.map((x) => <option key={x}>{x}</option>)}</select></Field>
            <Field label="Tax Period"><select className={selectCls} value={s.period} onChange={(e) => s.setPeriod(e.target.value)}>{PERIODS.map((x) => <option key={x}>{x}</option>)}</select></Field>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div><FileUploadDropzone multiple={false} accept=".xlsx,.xls,.csv" label="Purchase Register" hint="XLSX / CSV" onFiles={(f) => setPr(f[0])} />
              <p className="mt-1 flex items-center gap-1 text-xs">{pr ? <><CheckCircle2 className="h-3 w-3 text-success" />{pr.name}</> : <span className="text-muted-foreground">Not uploaded</span>}</p></div>
            <div><FileUploadDropzone multiple={false} accept=".json,.xlsx,.xls" label="GSTR-2B" hint="JSON or Excel" onFiles={(f) => setTwoB(f[0])} />
              <p className="mt-1 flex items-center gap-1 text-xs">{twoB ? <><CheckCircle2 className="h-3 w-3 text-success" />{twoB.name}</> : <span className="text-muted-foreground">Not uploaded</span>}</p></div>
          </div>
          <Button className="mt-3" disabled={!pr || !twoB} onClick={() => toast.success("Files received", { description: "Matching runs on the backend. Showing sample results." })}>Run reconciliation</Button>
        </Section>
        <Section title="Matching Tolerances">
          <div className="grid gap-3">
            <Field label="Tax difference tolerance (₹)"><Input type="number" className="num" value={tol.tax} onChange={(e) => setTol({ ...tol, tax: Number(e.target.value) })} /></Field>
            <Field label="Invoice date tolerance (days)"><Input type="number" className="num" value={tol.days} onChange={(e) => setTol({ ...tol, days: Number(e.target.value) })} /></Field>
            <label className="flex items-center gap-2 text-sm"><Switch checked={tol.normalize} onCheckedChange={(v) => setTol({ ...tol, normalize: v })} />Normalize invoice numbers</label>
          </div>
        </Section>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <KPICard label="ITC as per Books" value={fmtINR(itcBooks)} icon={IndianRupee} />
        <KPICard label="ITC as per 2B" value={fmtINR(itc2b)} icon={IndianRupee} />
        <KPICard label="Matched ITC" value={fmtINR(matched)} icon={CheckCircle2} tone="success" />
        <KPICard label="Deferred ITC" value={fmtINR(deferred)} icon={Clock} tone="warning" />
        <KPICard label="Unclaimed ITC" value={fmtINR(unclaimed)} icon={AlertTriangle} tone="warning" />
        <KPICard label="Net Variance" value={fmtINR(itcBooks - itc2b)} icon={Scale} tone="danger" />
      </div>

      <Tabs value={tab} onValueChange={setTab} className="mb-3">
        <TabsList className="h-auto flex-wrap">{TABS.map((x) => <TabsTrigger key={x.v} value={x.v}>{x.label}</TabsTrigger>)}</TabsList>
      </Tabs>
      <DataTable
        key={tab}
        rows={rows}
        rowKey={(r) => r.id}
        searchKeys={(r) => `${r.supplier} ${r.gstin} ${r.invoiceNo}`}
        selectable
        bulkActions={(ids, clear) => <Button size="sm" variant="outline" className="h-7" onClick={() => { ids.forEach((id) => update(id, { status: "Exact Match" })); toast.success(`${ids.length} match(es) approved`); clear(); }}>Approve matches</Button>}
        columns={[
          { key: "supplier", header: "Supplier" },
          { key: "gstin", header: "GSTIN", cell: (r) => <span className="num text-xs">{r.gstin}</span> },
          { key: "invoiceNo", header: "Invoice No.", cell: (r) => <span className="num">{r.invoiceNo}</span> },
          { key: "invoiceDate", header: "Date", cell: (r) => fmtDate(r.invoiceDate) },
          { key: "bt", header: "Books Taxable", align: "right", sortValue: (r) => r.booksTaxable ?? 0, cell: (r) => fmtINR(r.booksTaxable) },
          { key: "2t", header: "2B Taxable", align: "right", sortValue: (r) => r.twoBTaxable ?? 0, cell: (r) => fmtINR(r.twoBTaxable) },
          { key: "bg", header: "Books GST", align: "right", cell: (r) => fmtINR(r.booksGst) },
          { key: "2g", header: "2B GST", align: "right", cell: (r) => fmtINR(r.twoBGst) },
          { key: "d", header: "Difference", align: "right", sortValue: diff, cell: (r) => <span className={Math.abs(diff(r)) > tol.tax ? "text-destructive" : ""}>{fmtINR(diff(r))}</span> },
          { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
          {
            key: "a", header: "Actions", cell: (r) => (
              <div className="flex gap-1">
                <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="View comparison" onClick={() => { setOpen(r); setNote(r.note ?? ""); }}><Eye /></Button>
                <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Approve match" onClick={() => { update(r.id, { status: "Exact Match" }); toast.success("Match approved"); }}><Check /></Button>
                <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Mark for review" onClick={() => { update(r.id, { status: "Needs Review" }); toast.success("Marked for review"); }}><Flag /></Button>
                {tab === "vendor" && <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Draft vendor email" onClick={() => { navigator.clipboard?.writeText(`Dear ${r.supplier},\n\nInvoice ${r.invoiceNo} dated ${r.invoiceDate} is not reflecting correctly in our GSTR-2B. Kindly verify your GSTR-1 filing.\n\nRegards`); toast.success("Vendor email draft copied to clipboard"); }}><Mail /></Button>}
              </div>
            ),
          },
        ]}
      />

      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader><SheetTitle>Comparison · {open?.invoiceNo}</SheetTitle></SheetHeader>
          {open && (
            <div className="mt-4 space-y-4 text-sm">
              <div className="flex justify-between"><span>{open.supplier}</span><StatusBadge status={open.status} /></div>
              <table className="w-full rounded border">
                <thead className="bg-muted text-xs"><tr><th className="p-2 text-left">Field</th><th className="p-2 text-right">Purchase Register</th><th className="p-2 text-right">GSTR-2B</th></tr></thead>
                <tbody>
                  {[["GSTIN", open.gstin, open.twoBTaxable == null ? "—" : open.gstin], ["Invoice No.", open.invoiceNo, open.twoBTaxable == null ? "—" : open.invoiceNo], ["Taxable", fmtINR(open.booksTaxable), fmtINR(open.twoBTaxable)], ["GST", fmtINR(open.booksGst), fmtINR(open.twoBGst)]].map(([k, a, b]) => (
                    <tr key={k} className="border-t"><td className="p-2 text-muted-foreground">{k}</td><td className="num p-2 text-right">{a}</td><td className={`num p-2 text-right ${a !== b ? "text-destructive" : ""}`}>{b}</td></tr>
                  ))}
                </tbody>
              </table>
              <Field label="Internal note"><Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => { update(open.id, { note }); toast.success("Note saved"); }}><MessageSquare />Save note</Button>
                <Button onClick={() => { update(open.id, { status: "Exact Match", note }); setOpen(null); toast.success("Match approved"); }}><Check />Approve match</Button>
                <Button variant="outline" onClick={() => { update(open.id, { status: "Needs Review", note }); setOpen(null); }}><Flag />Mark for review</Button>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
