import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Play, Download, CheckCheck } from "lucide-react";
import {
  PageHeader,
  Section,
  KPICard,
  InfoNotice,
  StatusBadge,
  Field,
  EmptyState,
} from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCompany, useStore } from "@/lib/store";
import { FINANCIAL_YEARS, PERIODS } from "@/lib/constants";
import { useVendors } from "@/lib/queries/vendors";
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

const emptyR1 = (): Row[] => [
  { category: "B2B - Registered", taxable: 0, igst: 0, cgst: 0, sgst: 0 },
  { category: "B2C Large (Inter-state)", taxable: 0, igst: 0, cgst: 0, sgst: 0 },
  { category: "B2C Others", taxable: 0, igst: 0, cgst: 0, sgst: 0 },
  { category: "Exports (with payment)", taxable: 0, igst: 0, cgst: 0, sgst: 0 },
  { category: "Nil-rated / Exempt", taxable: 0, igst: 0, cgst: 0, sgst: 0 },
];

const emptyR3 = (): Row[] => [
  { category: "3.1(a) Outward taxable supplies", taxable: 0, igst: 0, cgst: 0, sgst: 0 },
  { category: "3.1(d) Inward supplies - reverse charge", taxable: 0, igst: 0, cgst: 0, sgst: 0 },
  { category: "4(A)(5) All other ITC - Eligible", taxable: 0, igst: 0, cgst: 0, sgst: 0 },
  { category: "4(B) ITC Reversed / Ineligible", taxable: 0, igst: 0, cgst: 0, sgst: 0 },
];

function EditableTable({ rows, onChange }: { rows: Row[]; onChange: (r: Row[]) => void }) {
  const tot = rows.reduce(
    (a, r) => ({
      taxable: a.taxable + r.taxable,
      igst: a.igst + r.igst,
      cgst: a.cgst + r.cgst,
      sgst: a.sgst + r.sgst,
    }),
    { taxable: 0, igst: 0, cgst: 0, sgst: 0 },
  );
  const keys = ["taxable", "igst", "cgst", "sgst"] as const;
  return (
    <div className="overflow-x-auto rounded-md border bg-card">
      <table className="w-full text-sm">
        <thead className="bg-muted text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2 text-left">Category</th>
            {["Taxable", "IGST", "CGST", "SGST"].map((h) => (
              <th key={h} className="px-3 py-2 text-right">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.category} className="border-t">
              <td className="px-3 py-1.5">{r.category}</td>
              {keys.map((k) => (
                <td key={k} className="px-1 py-1">
                  <Input
                    aria-label={`${r.category} ${k}`}
                    type="number"
                    className="num h-8 text-right"
                    value={r[k]}
                    onChange={(e) =>
                      onChange(
                        rows.map((x, j) =>
                          j === i ? { ...x, [k]: Number(e.target.value) || 0 } : x,
                        ),
                      )
                    }
                  />
                </td>
              ))}
            </tr>
          ))}
          <tr className="border-t bg-muted font-semibold">
            <td className="px-3 py-2">Total</td>
            {keys.map((k) => (
              <td key={k} className="num px-3 py-2 text-right">
                {fmtINR(tot[k])}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function GSTPage() {
  const s = useStore();
  const company = useCompany();
  const { data: vendors = [] } = useVendors(company?.id, true);
  const [r1, setR1] = useState(emptyR1);
  const [r3, setR3] = useState(emptyR3);
  const [key, setKey] = useState(company?.id ?? "");
  const [source, setSource] = useState("Books + Processed Invoices");
  const [status, setStatus] = useState<"Draft" | "Generated" | "Reviewed">("Draft");

  if (key !== (company?.id ?? "")) {
    setKey(company?.id ?? "");
    setR1(emptyR1());
    setR3(emptyR3());
    setStatus("Draft");
  }

  if (!company) {
    return (
      <>
        <PageHeader title="GST Workings" description="Prepare GSTR-1 and GSTR-3B workings." />
        <div className="rounded-md border bg-card p-4">
          <EmptyState
            title="No company selected"
            hint="Create a company before preparing GST workings."
          />
        </div>
      </>
    );
  }

  const exceptions = vendors
    .filter((v) => v.status !== "Inactive" && !v.gstin)
    .map((v) => ({ type: "Missing GSTIN", ref: v.name }));
  const out = r3[0] ?? { taxable: 0, igst: 0, cgst: 0, sgst: 0 };
  const rcm = r3[1] ?? { taxable: 0, igst: 0, cgst: 0, sgst: 0 };
  const itc = r3[2] ?? { taxable: 0, igst: 0, cgst: 0, sgst: 0 };
  const inel = r3[3] ?? { taxable: 0, igst: 0, cgst: 0, sgst: 0 };

  return (
    <>
      <PageHeader
        title="GST Workings"
        description={`${company.displayName} - ${s.financialYear} - ${s.period}`}
        actions={
          <>
            <Button
              onClick={() => {
                setStatus("Generated");
                toast.success("Working marked as generated from the values entered here");
              }}
            >
              <Play />
              Generate Working
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                exportSheets(`GST Working ${company.displayName} ${s.period}`, [
                  { name: "GSTR-1", rows: r1 },
                  { name: "GSTR-3B", rows: r3 },
                  { name: "Exceptions", rows: exceptions },
                ]);
                toast.success("3 CSV sheets downloaded");
              }}
            >
              <Download />
              Export Excel (CSV)
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                exportFile("CSV", `GST_Summary_${s.period}`, r3);
                toast.success("Report downloaded");
              }}
            >
              <Download />
              Download Report
            </Button>
            <Button
              variant="secondary"
              disabled={status === "Draft"}
              onClick={() => {
                setStatus("Reviewed");
                toast.success("Marked as reviewed");
              }}
            >
              <CheckCheck />
              Mark as Reviewed
            </Button>
          </>
        }
      />
      <InfoNotice>
        Workings are entered manually. PAL does not access the GST portal, compute tax
        automatically, or file returns.
      </InfoNotice>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Company">
          <select
            className={selectCls}
            value={company.id}
            onChange={(e) => s.setCompanyId(e.target.value)}
          >
            {s.companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.displayName}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Financial Year">
          <select
            className={selectCls}
            value={s.financialYear}
            onChange={(e) => s.setFinancialYear(e.target.value)}
          >
            {FINANCIAL_YEARS.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </Field>
        <Field label="Tax Period">
          <select
            className={selectCls}
            value={s.period}
            onChange={(e) => s.setPeriod(e.target.value)}
          >
            {PERIODS.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </Field>
        <Field label="Data Source">
          <select className={selectCls} value={source} onChange={(e) => setSource(e.target.value)}>
            {["Books + Processed Invoices", "Books only", "Uploaded Sales Register"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </Field>
        <div className="pb-1.5">
          Status: <StatusBadge status={status} />
        </div>
      </div>
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KPICard
          label="Taxable Outward"
          value={fmtINR(out.taxable)}
          icon={IndianRupee}
          hint={`IGST ${fmtINR(out.igst)}`}
        />
        <KPICard
          label="Output CGST / SGST"
          value={fmtINR(out.cgst + out.sgst)}
          icon={Receipt}
          hint={`${fmtINR(out.cgst)} each`}
        />
        <KPICard
          label="Eligible ITC"
          value={fmtINR(itc.igst + itc.cgst + itc.sgst)}
          icon={ShieldCheck}
          tone="success"
          hint={`Inward ${fmtINR(itc.taxable)}`}
        />
        <KPICard
          label="Ineligible ITC / RCM"
          value={fmtINR(inel.cgst + inel.sgst)}
          icon={Ban}
          tone="warning"
          hint={`RCM ${fmtINR(rcm.cgst + rcm.sgst)}`}
        />
      </div>
      <Tabs defaultValue="r1">
        <TabsList>
          <TabsTrigger value="r1">GSTR-1 Working</TabsTrigger>
          <TabsTrigger value="r3">GSTR-3B Working</TabsTrigger>
          <TabsTrigger value="ex">Exceptions ({exceptions.length})</TabsTrigger>
        </TabsList>
        <TabsContent value="r1" className="mt-3">
          <EditableTable rows={r1} onChange={setR1} />
        </TabsContent>
        <TabsContent value="r3" className="mt-3">
          <EditableTable rows={r3} onChange={setR3} />
        </TabsContent>
        <TabsContent value="ex" className="mt-3">
          <Section title="Exceptions">
            {exceptions.length ? (
              exceptions.map((e, i) => (
                <div key={i} className="flex justify-between border-b py-2 text-sm last:border-0">
                  <span>{e.ref}</span>
                  <StatusBadge status="Needs Review" />
                  <span className="w-56 text-right text-muted-foreground">{e.type}</span>
                </div>
              ))
            ) : (
              <EmptyState
                title="No exceptions found"
                hint="Vendors without a GSTIN will appear here."
              />
            )}
          </Section>
        </TabsContent>
      </Tabs>
    </>
  );
}
