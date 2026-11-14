import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Play, Download, RefreshCw } from "lucide-react";
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
import { useTallyGstWorking } from "@/lib/queries/tally";
import { useGstWorking } from "@/lib/queries/gst";
import type { GstWorkingRowDto } from "@/lib/api/gst";
import { FINANCIAL_YEARS, PERIODS, periodMonthStart } from "@/lib/constants";
import { useVendors } from "@/lib/queries/vendors";
import { fmtINR } from "@/lib/format";
import { exportFile, downloadBlob } from "@/lib/export";
import { tallyApi } from "@/lib/api/tally";
import { seo } from "@/lib/seo";
import { toast } from "sonner";
import { IndianRupee, Receipt, ShieldCheck, Ban } from "lucide-react";

export const Route = createFileRoute("/gst")({
  head: () => seo("GST Workings", "Prepare GSTR-1 and GSTR-3B workings and review exceptions."),
  component: GSTPage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";
type Row = { category: string; taxable: number; igst: number; cgst: number; sgst: number };

const TALLY_SOURCE = "TallyPrime vouchers";
const BOOKS_SOURCE = "Books + Processed Invoices";
const num = (v: string) => Number(v) || 0;

/** Fill rows from a backend working, matching on the category label. */
const applyWorking = (rows: Row[], dto: GstWorkingRowDto[]): Row[] => {
  const by = new Map(dto.map((d) => [d.category, d]));
  return rows.map((r) => {
    const d = by.get(r.category);
    return d
      ? { ...r, taxable: num(d.taxable), igst: num(d.igst), cgst: num(d.cgst), sgst: num(d.sgst) }
      : r;
  });
};

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
  const [source, setSource] = useState(TALLY_SOURCE);
  const [status, setStatus] = useState<"Draft" | "Generated" | "Reviewed">("Draft");
  // True once a manual Tally pull has filled the tables, so the Books auto-fill
  // does not overwrite it. Cleared when the period, company or source changes.
  const [tallyFilled, setTallyFilled] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const gstSync = useTallyGstWorking();
  // Last company|period auto-synced from Tally, so the auto-sync fires once per
  // period rather than on every render.
  const autoSyncedKey = useRef("");

  // The selected FY + tax period as an inclusive [start, end] date range.
  const start = periodMonthStart(s.financialYear, s.period);
  let end: string | undefined;
  if (start) {
    const y = Number(start.slice(0, 4));
    const m = Number(start.slice(5, 7));
    const lastDay = new Date(y, m, 0).getDate();
    end = `${start.slice(0, 7)}-${String(lastDay).padStart(2, "0")}`;
  }

  // Working computed from PAL's own processed invoices (buyer GSTIN + place of
  // supply), the only source that can split GSTR-1 by B2B/B2C/export/nil.
  const fromBooks = source === BOOKS_SOURCE;
  const fromTally = source === TALLY_SOURCE;
  // Under Tally we still need the invoice-derived GSTR-1 (Tally vouchers carry
  // no customer GSTIN), so fetch Books for either source.
  const books = useGstWorking(company?.id, start, end, fromBooks || fromTally);
  useEffect(() => {
    if (!books.data) return;
    if (fromBooks) {
      setR1((rows) => applyWorking(rows, books.data!.gstr1));
      // GSTR-3B from Books only until a manual Tally pull supplies the totals.
      if (!tallyFilled) setR3((rows) => applyWorking(rows, books.data!.gstr3b));
      setStatus("Generated");
    } else if (fromTally) {
      // GSTR-1 from Books; GSTR-3B is filled by the Tally sync, not here.
      setR1((rows) => applyWorking(rows, books.data!.gstr1));
    }
  }, [fromBooks, fromTally, tallyFilled, books.data]);
  // A new period or company means the pulled Tally numbers no longer apply, so
  // let Books take over again until the next manual sync.
  useEffect(() => setTallyFilled(false), [start, end, company?.id]);

  const syncFromTally = () => {
    const name = company?.legalName ?? company?.displayName ?? "";
    if (!company || !name) return toast.error("This company has no name to match in Tally.");
    gstSync.mutate(
      { companyId: company.id, tallyCompanyName: name, fromDate: start, toDate: end },
      {
        onSuccess: (d) => {
          if (!d.success) return toast.error(`Tally sync failed: ${d.error_message}`);
          const o = d.outward;
          const i = d.inward_itc;
          // Tally supplies GSTR-3B totals only. The GSTR-1 category split (B2B/
          // B2C/export/nil) needs buyer GSTIN + place of supply, which Tally
          // vouchers do not carry, so it comes from the Books source instead.
          setR3((rows) =>
            rows.map((r) => {
              if (r.category === "3.1(a) Outward taxable supplies")
                return { ...r, taxable: num(o.taxable), igst: num(o.igst), cgst: num(o.cgst), sgst: num(o.sgst) };
              if (r.category === "4(A)(5) All other ITC - Eligible")
                return { ...r, taxable: num(i.taxable), igst: num(i.igst), cgst: num(i.cgst), sgst: num(i.sgst) };
              return r;
            }),
          );
          setStatus("Generated");
          setTallyFilled(true);
          if (d.vouchers_used === 0 && d.vouchers_fetched > 0) {
            toast.warning(
              `Tally returned ${d.vouchers_fetched} voucher(s), but none fall in ${s.period} ${s.financialYear}. Check the Tax Period.`,
            );
          } else {
            toast.success(`Pulled ${d.vouchers_used} voucher(s) from Tally`);
          }
        },
        onError: (e) => toast.error(e.message),
      },
    );
  };

  // Build and download the full working as .xlsx in the client's layout. The
  // workbook is generated server-side from the same Tally vouchers, so it always
  // matches the on-screen GSTR-3B totals.
  const downloadWorkbook = async () => {
    const name = company?.legalName ?? company?.displayName ?? "";
    if (!company || !name) return toast.error("This company has no name to match in Tally.");
    setDownloading(true);
    try {
      const file = await tallyApi.gstWorkbook(company.id, name, start, end);
      downloadBlob(file.filename, file.blob);
      setStatus("Generated");
      toast.success(`Downloaded ${file.filename}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not build the workbook.");
    } finally {
      setDownloading(false);
    }
  };

  // TallyPrime is the primary source: when it is selected, pull the period's
  // vouchers automatically (once per company+period) so the working generates
  // itself, the same way the Books source auto-fills.
  useEffect(() => {
    if (source !== TALLY_SOURCE || !company || !start || gstSync.isPending) return;
    const k = `${company.id}|${start}|${end ?? ""}`;
    if (autoSyncedKey.current === k) return;
    autoSyncedKey.current = k;
    syncFromTally();
    // syncFromTally is recreated each render; depend on the inputs it reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, company?.id, start, end]);

  if (key !== (company?.id ?? "")) {
    setKey(company?.id ?? "");
    setR1(emptyR1());
    setR3(emptyR3());
    setStatus("Draft");
    setTallyFilled(false);
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
            <Button variant="outline" onClick={syncFromTally} disabled={gstSync.isPending}>
              <RefreshCw className={gstSync.isPending ? "animate-spin" : undefined} />
              {gstSync.isPending ? "Pulling…" : "Sync from Tally"}
            </Button>
            <Button onClick={downloadWorkbook} disabled={downloading}>
              <Play className={downloading ? "animate-spin" : undefined} />
              {downloading ? "Generating…" : "Generate Working"}
            </Button>
            <Button variant="outline" onClick={downloadWorkbook} disabled={downloading}>
              <Download />
              Export Excel (.xlsx)
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
          </>
        }
      />
      <InfoNotice>
        {fromBooks
          ? `GSTR-1 and GSTR-3B are computed from ${books.data?.invoices_used ?? 0} processed invoice(s) in this period; every cell stays editable. Reverse charge and ineligible ITC need a manual entry.`
          : fromTally
            ? `GSTR-3B totals come from the period's Tally vouchers; the GSTR-1 split comes from ${books.data?.invoices_used ?? 0} processed invoice(s), since Tally vouchers carry no customer GSTIN. Every cell stays editable.`
            : "Everything stays editable."}{" "}
        PAL does not access the GST portal or file returns.
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
          <select
            className={selectCls}
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setTallyFilled(false);
            }}
          >
            {[TALLY_SOURCE, "Books + Processed Invoices", "Books only", "Uploaded Sales Register"].map((x) => (
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
