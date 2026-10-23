import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { CircleDashed, FileSpreadsheet, Eye } from "lucide-react";
import { PageHeader, Section, KV, StatusBadge, Field, InfoNotice, EmptyState } from "@/components/pal/common";
import { ExportHistory } from "@/components/pal/export-history";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useCompany, useStore } from "@/lib/store";
import { exportFile } from "@/lib/export";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/zoho")({
  head: () => seo("Zoho Books", "Configure Zoho Books vendor, account and tax mapping; export bills as CSV."),
  component: ZohoPage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";
const COLUMNS = ["Bill Date", "Bill Number", "Vendor Name", "GST Identification Number (GSTIN)", "Account", "Item Total", "Tax Name", "Tax Percentage"];

function ZohoPage() {
  const s = useStore();
  const company = useCompany();
  const [org, setOrg] = useState("");
  const [cfg, setCfg] = useState({ vendorMatch: "GSTIN", expenseAccount: "", taxName: "GST18", importDrafts: true, attachFiles: false });
  const [err, setErr] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState(false);

  if (!company) {
    return (
      <>
        <PageHeader title="Zoho Books" description="Bills import configuration and CSV exports for Zoho Books." />
        <div className="rounded-md border bg-card p-4">
          <EmptyState title="No company selected" hint="Create a company before configuring a Zoho Books connection." />
        </div>
      </>
    );
  }

  const validate = () => {
    const e: Record<string, string> = {};
    if (!org) e.org = "Select an organization";
    if (!cfg.expenseAccount.trim()) e.expenseAccount = "Default expense account is required";
    if (!cfg.taxName.trim()) e.taxName = "Tax name is required";
    setErr(e);
    return !Object.keys(e).length;
  };
  // Bill rows are produced from processed invoices once the Zoho export
  // backend exists. The configuration page does not invent placeholder bills.
  const rows: Record<string, string | number>[] = [];
  const run = () => {
    if (!validate()) return toast.error("Fix the highlighted fields first.");
    if (!rows.length) return toast.error("No invoices are available to export yet.");
    const n = exportFile("CSV", `ZohoBills_${company.displayName.replace(/\s/g, "")}`, rows, COLUMNS);
    if (n == null) return toast.error("CSV export is not available.");
    s.addExport({ name: "Bills import", type: "Bills", format: "CSV", records: n, status: "Generated", target: "Zoho Books" });
    toast.success(`${n} bill${n === 1 ? "" : "s"} exported`);
  };

  return (
    <>
      <PageHeader title="Zoho Books" description="Bills import configuration and CSV exports for Zoho Books." />
      <InfoNotice>No Zoho account is connected. Configuration is kept in this browser session only; bill export will be added by the backend.</InfoNotice>
      <div className="grid gap-4 lg:grid-cols-3">
        <Section title="Connection Status">
          <div className="mb-3 flex items-center gap-2"><CircleDashed className="h-4 w-4 text-muted-foreground" /><StatusBadge status="Not connected" /></div>
          <Field label="Organization" error={err.org}>
            <select className={selectCls} value={org} onChange={(e) => setOrg(e.target.value)}>
              <option value="">— Select organization —</option>
              <option>{company.legalName}</option>
            </select>
          </Field>
          <div className="mt-2"><KV k="Last synchronization" v="Never" /></div>
        </Section>
        <Section title="Mapping & Bills Import" className="lg:col-span-2">
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Vendor mapping key"><select className={selectCls} value={cfg.vendorMatch} onChange={(e) => setCfg({ ...cfg, vendorMatch: e.target.value })}>{["GSTIN", "PAN", "Vendor Name"].map((x) => <option key={x}>{x}</option>)}</select></Field>
            <Field label="Default expense account" error={err.expenseAccount}><Input value={cfg.expenseAccount} onChange={(e) => setCfg({ ...cfg, expenseAccount: e.target.value })} placeholder="e.g. Office Expenses" /></Field>
            <Field label="Tax mapping (Zoho tax name)" error={err.taxName}><Input value={cfg.taxName} onChange={(e) => setCfg({ ...cfg, taxName: e.target.value })} /></Field>
          </div>
          <div className="mt-4 flex flex-wrap gap-6 text-sm">
            <label className="flex items-center gap-2"><Switch checked={cfg.importDrafts} onCheckedChange={(v) => setCfg({ ...cfg, importDrafts: v })} />Import bills as drafts</label>
            <label className="flex items-center gap-2"><Switch checked={cfg.attachFiles} onCheckedChange={(v) => setCfg({ ...cfg, attachFiles: v })} />Attach original documents</label>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => validate() && toast.success("Settings valid and saved in this browser session")}>Validate & save</Button>
            <Button variant="outline" onClick={() => setPreview(true)}><Eye />Preview export columns</Button>
            <Button onClick={run} disabled={!rows.length}><FileSpreadsheet />Generate CSV</Button>
          </div>
        </Section>
        <div className="lg:col-span-3">
          <h2 className="mb-2 text-sm font-semibold">Export History</h2>
          <ExportHistory target="Zoho Books" />
        </div>
      </div>
      <Dialog open={preview} onOpenChange={setPreview}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>Export column preview</DialogTitle></DialogHeader>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-muted"><tr>{COLUMNS.map((c) => <th key={c} className="whitespace-nowrap px-2 py-1.5 text-left">{c}</th>)}</tr></thead>
              <tbody>{rows.slice(0, 5).map((r, i) => <tr key={i} className="border-t">{COLUMNS.map((c) => <td key={c} className="whitespace-nowrap px-2 py-1.5">{String(r[c as keyof typeof r])}</td>)}</tr>)}</tbody>
            </table>
            {!rows.length && <p className="py-8 text-center text-sm text-muted-foreground">No bill rows are available yet.</p>}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
