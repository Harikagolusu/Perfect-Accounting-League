import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Download, Eye, FileBarChart } from "lucide-react";
import { PageHeader, InfoNotice, Section, EmptyState } from "@/components/pal/common";
import { ExportHistory } from "@/components/pal/export-history";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FilterSelect } from "@/components/pal/data-table";
import { useCompany, useScoped, useStore } from "@/lib/store";
import { useBankStatements } from "@/lib/queries/bankStatements";
import { useBankAccounts } from "@/lib/queries/bankAccounts";
import { useDocuments } from "@/lib/queries/documents";
import { useVendors } from "@/lib/queries/vendors";
import { useAccountingAudit } from "@/lib/queries/accountingAudit";
import { useGstWorking } from "@/lib/queries/gst";
import { useTallyTdsWorkingReport } from "@/lib/queries/tally";
import { useInvoices } from "@/lib/queries/invoices";
import { exportFile, type ExportFormat } from "@/lib/export";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/reports")({
  head: () =>
    seo(
      "Reports & Exports",
      "Generate and download invoice, GST, TDS, reconciliation and audit reports.",
    ),
  component: ReportsPage,
});

function ReportsPage() {
  const s = useStore();
  const company = useCompany();
  const { data: docs = [] } = useDocuments(company?.id);
  const { data: vendors = [] } = useVendors(company?.id, true);
  const reco = useScoped(s.reco);
  const { data: statements = [] } = useBankStatements(company?.id);
  const { data: bankAccounts = [] } = useBankAccounts(company?.id);
  const { data: audit = [] } = useAccountingAudit(company?.id);
  const [q, setQ] = useState("");
  const [type, setType] = useState("All");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [preview, setPreview] = useState<string | null>(null);
  // Real GSTR-1/GSTR-3B working from the backend (same /gst/working the GST
  // Workings page uses), so the GST Working Report shows computed numbers.
  const { data: gstWorking } = useGstWorking(company?.id, from || undefined, to || undefined);
  // TDS working from TallyPrime, same source the TDS Workings page uses. The
  // chosen date range, or the whole financial year when no range is set.
  const tdsName = company?.legalName ?? company?.displayName;
  const fyStart = Number(s.financialYear.match(/\d{4}/)?.[0]);
  const tdsFrom = from || (fyStart ? `${fyStart}-04-01` : undefined);
  const tdsTo = to || (fyStart ? `${fyStart + 1}-03-31` : undefined);
  const { data: tdsWorking } = useTallyTdsWorkingReport(company?.id, tdsName, tdsFrom, tdsTo);
  const { data: invoices = [] } = useInvoices(company?.id);
  const inRange = (d: string) => (!from || d >= from) && (!to || d.slice(0, 10) <= to);

  if (!company) {
    return (
      <>
        <PageHeader
          title="Reports & Exports"
          description="Generate and download accounting reports."
        />
        <div className="rounded-md border bg-card p-4">
          <EmptyState
            title="No company selected"
            hint="Create a company before generating reports."
          />
        </div>
      </>
    );
  }

  const REPORTS: { name: string; cat: string; build: () => Record<string, unknown>[] }[] = [
    {
      name: "Invoice Processing Report",
      cat: "Processing",
      build: () =>
        docs
          .filter((d) => inRange(d.receivedAt))
          .map((d) => ({
            ID: d.id,
            Name: d.name,
            Source: d.source,
            Received: d.receivedAt,
            Status: d.status,
            Reviewer: d.reviewer,
          })),
    },
    {
      name: "Purchase Register Report",
      cat: "Processing",
      build: () =>
        invoices
          .filter((i) => i.direction === "PURCHASE" && i.invoiceDate && inRange(i.invoiceDate))
          .map((i) => ({
            Supplier: i.vendorName,
            GSTIN: i.vendorGstin,
            InvoiceNo: i.invoiceNumber,
            Date: i.invoiceDate,
            Taxable: Number(i.taxableAmount) || 0,
            GST:
              (Number(i.igstAmount) || 0) +
              (Number(i.cgstAmount) || 0) +
              (Number(i.sgstAmount) || 0),
          })),
    },
    {
      name: "GST Working Report",
      cat: "Tax",
      build: () => {
        const w = gstWorking;
        if (!w) return [];
        const rows = (section: string, list: typeof w.gstr1) =>
          list.map((r) => ({
            Section: section,
            Category: r.category,
            Taxable: Number(r.taxable),
            IGST: Number(r.igst),
            CGST: Number(r.cgst),
            SGST: Number(r.sgst),
          }));
        return [...rows("GSTR-1", w.gstr1), ...rows("GSTR-3B", w.gstr3b)];
      },
    },
    {
      name: "TDS Working Report",
      cat: "Tax",
      build: () => {
        if (!tdsWorking || !tdsWorking.success) return [];
        return tdsWorking.rows.map((t) => ({
          Date: t.voucher_date,
          Voucher: t.voucher_number,
          Vendor: t.party_name,
          PAN: t.pan,
          Section: t.section,
          Base: Number(t.base),
          TDS: Number(t.tds),
          Rate: Number(t.rate),
        }));
      },
    },
    {
      name: "GST Reconciliation Report",
      cat: "Tax",
      build: () =>
        reco
          .filter((r) => inRange(r.invoiceDate))
          .map((r) => ({
            Supplier: r.supplier,
            InvoiceNo: r.invoiceNo,
            BooksGST: r.booksGst,
            TwoBGST: r.twoBGst,
            Status: r.status,
          })),
    },
    {
      name: "Bank Statement Report",
      cat: "Processing",
      build: () => {
        const acct = (id: string) => bankAccounts.find((a) => a.id === id);
        return statements
          .filter((st) => inRange(st.statementStartDate ?? st.statementEndDate ?? ""))
          .map((st) => {
            const a = acct(st.bankAccountId);
            return {
              BankAccount: a ? `${a.bankName} · ${a.accountName}` : st.bankAccountId,
              AccountNo: a?.maskedAccountNumber ?? "—",
              PeriodStart: st.statementStartDate ?? "—",
              PeriodEnd: st.statementEndDate ?? "—",
              Opening: st.openingBalance ?? "—",
              Closing: st.closingBalance ?? "—",
              Currency: st.currency,
              Status: st.status,
            };
          });
      },
    },
    {
      name: "Vendor Ledger Grouping Report",
      cat: "Masters",
      build: () =>
        vendors.map((v) => ({
          Vendor: v.name,
          GSTIN: v.gstin,
          PAN: v.pan,
          DefaultExpenseLedger: v.defaultExpenseLedger,
          CreditorLedger: v.creditorLedger,
          ParentGroup: v.parentGroup,
          Status: v.status,
        })),
    },
    {
      name: "Audit Trail Report",
      cat: "Control",
      build: () =>
        audit
          .filter((a) => inRange(a.timestamp))
          .map((a) => ({
            Time: a.timestamp,
            User: a.user,
            Module: a.module,
            Action: a.action,
            Record: a.recordId,
            Status: a.status,
          })),
    },
  ];
  const list = REPORTS.filter(
    (r) => (type === "All" || r.cat === type) && r.name.toLowerCase().includes(q.toLowerCase()),
  );
  const pv = REPORTS.find((r) => r.name === preview);
  const pvRows = pv?.build() ?? [];

  const download = (name: string, format: ExportFormat = "CSV") => {
    const rows = REPORTS.find((r) => r.name === name)!.build();
    if (!rows.length)
      return toast.error("No records match the selected filters — nothing to export.");
    const n = exportFile(
      format,
      `${name.replace(/\s/g, "_")}_${company.displayName.replace(/\s/g, "")}`,
      rows,
    );
    if (n == null)
      return toast.error(
        `${format} export is generated by the backend and isn't available in this preview.`,
      );
    s.addExport({
      name,
      type: "Report",
      format,
      records: n,
      status: "Generated",
      target: "Report",
    });
    toast.success(`${name} downloaded (${n} rows)`);
  };

  return (
    <>
      <PageHeader
        title="Reports & Exports"
        description={`Reports for ${company.displayName}. CSV files are generated in your browser.`}
      />
      <InfoNotice>
        Reports are built from records currently available in the app. Modules without a backend
        contribute no rows; XLSX export will be provided by the backend.
      </InfoNotice>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search reports…"
          className="h-9 w-64"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search reports"
        />
        <Input
          type="date"
          className="h-9 w-36"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          aria-label="From date"
        />
        <Input
          type="date"
          className="h-9 w-36"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          aria-label="To date"
        />
        <FilterSelect
          label="Type"
          value={type}
          onChange={setType}
          options={["Processing", "Tax", "Masters", "Control"]}
        />
        <select
          aria-label="Company"
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          value={s.companyId}
          onChange={(e) => s.setCompanyId(e.target.value)}
        >
          {s.companies.map((c) => (
            <option key={c.id} value={c.id}>
              {c.displayName}
            </option>
          ))}
        </select>
      </div>
      <div className="mb-6 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {list.map((r) => (
          <div key={r.name} className="flex flex-col gap-3 rounded-md border bg-card p-4">
            <div className="flex items-start gap-2">
              <FileBarChart className="mt-0.5 h-4 w-4 text-primary" />
              <div>
                <div className="text-sm font-medium">{r.name}</div>
                <div className="text-xs text-muted-foreground">
                  {r.cat} · {r.build().length} records
                </div>
              </div>
            </div>
            <div className="mt-auto flex gap-2">
              <Button size="sm" variant="outline" onClick={() => setPreview(r.name)}>
                <Eye />
                Preview
              </Button>
              <Button size="sm" onClick={() => download(r.name)}>
                <Download />
                CSV
              </Button>
              <Button size="sm" variant="ghost" onClick={() => download(r.name, "XLSX")}>
                XLSX
              </Button>
            </div>
          </div>
        ))}
      </div>
      <Section title="Export History">
        <ExportHistory />
      </Section>
      <Dialog open={!!preview} onOpenChange={(o) => !o && setPreview(null)}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>{preview}</DialogTitle>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-auto">
            {pvRows.length ? (
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-muted">
                  <tr>
                    {Object.keys(pvRows[0]).map((k) => (
                      <th key={k} className="px-2 py-1.5 text-left">
                        {k}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pvRows.slice(0, 25).map((r, i) => (
                    <tr key={i} className="border-t">
                      {Object.values(r).map((v, j) => (
                        <td key={j} className="whitespace-nowrap px-2 py-1.5">
                          {String(v ?? "—")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No records match the filters.
              </p>
            )}
          </div>
          <DialogFooter>
            <Button onClick={() => preview && download(preview)}>
              <Download />
              Download CSV
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
