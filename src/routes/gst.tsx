import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { Play, Download, CheckCheck } from "lucide-react";
import { PageHeader, Section, KPICard, DemoNotice, StatusBadge, Field } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCompany, useScoped, useStore } from "@/lib/store";
import { FINANCIAL_YEARS, PERIODS } from "@/lib/mock-data";
import { fmtINR } from "@/lib/format";
import { exportFile, exportSheets } from "@/lib/export";
import { seo } from "@/lib/seo";
import { toast } from "sonner";
import { IndianRupee, Receipt, ShieldCheck, Ban } from "lucide-react";

export const Route = createFileRoute("/gst")({
  head: () => seo("GST Workings", "Prepare GSTR-1 and GSTR-3B workings and review exceptions."),
  component: GSTPage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";
type Row = { category: string; taxable: number; igst: number; cgst: number; sgst: number };

function seed(companyIdx: number): { r1: Row[]; r3: Row[] } {
  const k = 1 + companyIdx * 0.23;
  const r = (n: number) => Math.round(n * k);
  return {
    r1: [
      { category: "B2B — Registered", taxable: r(4820000), igst: r(312000), cgst: r(278000), sgst: r(278000) },
      { category: "B2C Large (Inter-state)", taxable: r(640000), igst: r(115200), cgst: 0, sgst: 0 },
      { category: "B2C Others", taxable: r(915000), igst: 0, cgst: r(54900), sgst: r(54900) },
      { category: "Exports (with payment)", taxable: r(1210000), igst: r(217800), cgst: 0, sgst: 0 },
      { category: "Nil-rated / Exempt", taxable: r(184000), igst: 0, cgst: 0, sgst: 0 },
    ],
    r3: [
      { category: "3.1(a) Outward taxable supplies", taxable: r(6375000), igst: r(427200), cgst: r(332900), sgst: r(332900) },
      { category: "3.1(d) Inward supplies — reverse charge", taxable: r(210000), igst: 0, cgst: r(18900), sgst: r(18900) },
      { category: "4(A)(5) All other ITC — Eligible", taxable: r(3920000), igst: r(198000), cgst: r(176400), sgst: r(176400) },
      { category: "4(B) ITC Reversed / Ineligible", taxable: r(145000), igst: 0, cgst: r(13050), sgst: r(13050) },
    ],
  };
}

function EditableTable({ rows, onChange }: { rows: Row[]; onChange: (r: Row[]) => void }) {
  const tot = rows.reduce((a, r) => ({ taxable: a.taxable + r.taxable, igst: a.igst + r.igst, cgst: a.cgst + r.cgst, sgst: a.sgst + r.sgst }), { taxable: 0, igst: 0, cgst: 0, sgst: 0 });
  const keys = ["taxable", "igst", "cgst", "sgst"] as const;
  return (
    <div className="overflow-x-auto rounded-md border bg-card">
      <table className="w-full text-sm">
        <thead className="bg-muted text-xs uppercase text-muted-foreground"><tr><th className="px-3 py-2 text-left">Category</th>{["Taxable", "IGST", "CGST", "SGST"].map((h) => <th key={h} className="px-3 py-2 text-right">{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.category} className="border-t">
              <td className="px-3 py-1.5">{r.category}</td>
              {keys.map((k) => (
                <td key={k} className="px-1 py-1"><Input aria-label={`${r.category} ${k}`} type="number" className="num h-8 text-right" value={r[k]} onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, [k]: Number(e.target.value) || 0 } : x)))} /></td>
              ))}
            </tr>
          ))}
          <tr className="border-t bg-muted font-semibold"><td className="px-3 py-2">Total</td>{keys.map((k) => <td key={k} className="num px-3 py-2 text-right">{fmtINR(tot[k])}</td>)}</tr>
        </tbody>
      </table>
    </div>
  );
}

function GSTPage() {
  const s = useStore();
  const company = useCompany();
  const idx = s.companies.findIndex((c) => c.id === s.companyId);
  const base = useMemo(() => seed(Math.max(0, idx)), [idx]);
  const [r1, setR1] = useState(base.r1);
  const [r3, setR3] = useState(base.r3);
  const [key, setKey] = useState(s.companyId);
  const [source, setSource] = useState("Books + Processed Invoices");
  const [status, setStatus] = useState<"Draft" | "Generated" | "Reviewed">("Draft");
  if (key !== s.companyId) { setKey(s.companyId); setR1(base.r1); setR3(base.r3); setStatus("Draft"); }

  const vendors = useScoped(s.vendors);
  const exceptions = [
    ...vendors.filter((v) => !v.gstin).map((v) => ({ type: "Missing GSTIN", ref: v.name })),
    { type: "Invalid GSTIN format", ref: "INV KAV/2611 — 27AAJC1234" },
    { type: "Tax calculation difference", ref: "DEC/26-27/412 — ₹212 variance" },
    { type: "Duplicate invoice", ref: "SHR/2604 appears twice" },
    { type: "Missing tax classification", ref: "BLU/26-27/118 — HSN blank" },
  ];
  const out = r3[0], rcm = r3[1], itc = r3[2], inel = r3[3];

  return (
    <>
      <PageHeader title="GST Workings" description={`${company.displayName} · ${s.financialYear} · ${s.period}`}
        actions={<>
          <Button onClick={() => { setStatus("Generated"); toast.success("Working generated from sample data"); }}><Play />Generate Working</Button>
          <Button variant="outline" onClick={() => { exportSheets(`GST Working ${company.displayName} ${s.period}`, [{ name: "GSTR-1", rows: r1 }, { name: "GSTR-3B", rows: r3 }, { name: "Exceptions", rows: exceptions }]); toast.success("3 CSV sheets downloaded"); }}><Download />Export Excel (CSV)</Button>
          <Button variant="outline" onClick={() => { exportFile("CSV", `GST_Summary_${s.period}`, r3); toast.success("Report downloaded"); }}><Download />Download Report</Button>
          <Button variant="secondary" disabled={status === "Draft"} onClick={() => { setStatus("Reviewed"); s.log({ module: "GST Workings", action: "Approved", recordId: `GST-${s.period}`, previous: "Generated", next: "Reviewed" }); toast.success("Marked as reviewed"); }}><CheckCheck />Mark as Reviewed</Button>
        </>} />
      <DemoNotice>Demo calculations on sample data — not statutory computations. PAL does not file GST returns.</DemoNotice>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Company"><select className={selectCls} value={s.companyId} onChange={(e) => s.setCompanyId(e.target.value)}>{s.companies.map((c) => <option key={c.id} value={c.id}>{c.displayName}</option>)}</select></Field>
        <Field label="Financial Year"><select className={selectCls} value={s.financialYear} onChange={(e) => s.setFinancialYear(e.target.value)}>{FINANCIAL_YEARS.map((x) => <option key={x}>{x}</option>)}</select></Field>
        <Field label="Tax Period"><select className={selectCls} value={s.period} onChange={(e) => s.setPeriod(e.target.value)}>{PERIODS.map((x) => <option key={x}>{x}</option>)}</select></Field>
        <Field label="Data Source"><select className={selectCls} value={source} onChange={(e) => setSource(e.target.value)}>{["Books + Processed Invoices", "Books only", "Uploaded Sales Register"].map((x) => <option key={x}>{x}</option>)}</select></Field>
        <div className="pb-1.5">Status: <StatusBadge status={status} /></div>
      </div>
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KPICard label="Taxable Outward" value={fmtINR(out.taxable)} icon={IndianRupee} hint={`IGST ${fmtINR(out.igst)}`} />
        <KPICard label="Output CGST / SGST" value={fmtINR(out.cgst + out.sgst)} icon={Receipt} hint={`${fmtINR(out.cgst)} each`} />
        <KPICard label="Eligible ITC" value={fmtINR(itc.igst + itc.cgst + itc.sgst)} icon={ShieldCheck} tone="success" hint={`Inward ${fmtINR(itc.taxable)}`} />
        <KPICard label="Ineligible ITC / RCM" value={fmtINR(inel.cgst + inel.sgst)} icon={Ban} tone="warning" hint={`RCM ${fmtINR(rcm.cgst + rcm.sgst)}`} />
      </div>
      <Tabs defaultValue="r1">
        <TabsList><TabsTrigger value="r1">GSTR-1 Working</TabsTrigger><TabsTrigger value="r3">GSTR-3B Working</TabsTrigger><TabsTrigger value="ex">Exceptions ({exceptions.length})</TabsTrigger></TabsList>
        <TabsContent value="r1" className="mt-3"><EditableTable rows={r1} onChange={setR1} /></TabsContent>
        <TabsContent value="r3" className="mt-3"><EditableTable rows={r3} onChange={setR3} /></TabsContent>
        <TabsContent value="ex" className="mt-3">
          <Section title="Exceptions">
            {exceptions.map((e, i) => <div key={i} className="flex justify-between border-b py-2 text-sm last:border-0"><span>{e.ref}</span><StatusBadge status={e.type === "Duplicate invoice" ? "Exception" : "Needs Review"} /><span className="w-56 text-right text-muted-foreground">{e.type}</span></div>)}
          </Section>
        </TabsContent>
      </Tabs>
    </>
  );
}
