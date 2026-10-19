import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { CircleDashed, FileSpreadsheet, Eye } from "lucide-react";
import { PageHeader, Section, KV, StatusBadge, Field, DemoNotice } from "@/components/pal/common";
import { ExportHistory } from "@/components/pal/export-history";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useCompany, useScoped, useStore } from "@/lib/store";
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
  const mappings = useScoped(s.mappings);
  const [org, setOrg] = useState("");
  const [cfg, setCfg] = useState({ vendorMatch: "GSTIN", expenseAccount: "", taxName: "GST18", importDrafts: true, attachFiles: false });
  const [err, setErr] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState(false);

  const validate = () => {
    const e: Record<string, string> = {};
    if (!org) e.org = "Select an organization";
    if (!cfg.expenseAccount.trim()) e.expenseAccount = "Default expense account is required";
    if (!cfg.taxName.trim()) e.taxName = "Tax name is required";
    setErr(e);
    return !Object.keys(e).length;
  };
  const rows = mappings.map((m, i) => ({
    "Bill Date": `2026-10-${String((i % 27) + 1).padStart(2, "0")}`, "Bill Number": `DEMO-${100 + i}`, "Vendor Name": m.vendorName,
    "GST Identification Number (GSTIN)": m.vendorKey, Account: m.expenseLedger || cfg.expenseAccount, "Item Total": 10000 + i * 731, "Tax Name": cfg.taxName, "Tax Percentage": 18,
  }));
  const run = () => {
    if (!validate()) return toast.error("Fix the highlighted fields first.");
    const n = exportFile("CSV", `ZohoBills_${company.displayName.replace(/\s/g, "")}_DEMO`, rows, COLUMNS);
    s.addExport({ name: "Bills import (demo)", type: "Bills", format: "CSV", records: n ?? 0, status: "Generated", target: "Zoho Books" });
    toast.success(`Demo CSV downloaded (${n} bills)`);
  };

  return (
    <>
      <PageHeader title="Zoho Books" description="Bills import configuration and CSV exports for Zoho Books." />
      <DemoNotice>No Zoho account is connected. The REST API connection will be added by your engineering team.</DemoNotice>
      <div className="grid gap-4 lg:grid-cols-3">
        <Section title="Connection Status">
          <div className="mb-3 flex items-center gap-2"><CircleDashed className="h-4 w-4 text-muted-foreground" /><StatusBadge status="Not connected" /></div>
          <Field label="Organization" error={err.org}>
            <select className={selectCls} value={org} onChange={(e) => setOrg(e.target.value)}>
              <option value="">— Select organization —</option>
              <option>{company.legalName} (demo)</option>
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
            <Button variant="outline" onClick={() => validate() && toast.success("Settings valid and saved (demo state)")}>Validate & save</Button>
            <Button variant="outline" onClick={() => setPreview(true)}><Eye />Preview export columns</Button>
            <Button onClick={run}><FileSpreadsheet />Generate demo CSV</Button>
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
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
