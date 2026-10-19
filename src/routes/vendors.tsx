import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Plus, Pencil, History, Check } from "lucide-react";
import { PageHeader, StatusBadge, ConfidenceBadge, Field, DemoNotice, KV } from "@/components/pal/common";
import { DataTable, FilterSelect } from "@/components/pal/data-table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useCompany, useScoped, useStore } from "@/lib/store";
import { EXPENSE_LEDGERS, PARENT_GROUPS } from "@/lib/mock-data";
import { fmtDate, GSTIN_RE, PAN_RE } from "@/lib/format";
import type { LedgerMapping, Vendor } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/vendors")({
  head: () => seo("Vendor Master", "Vendors, default ledgers and historical ledger mappings per company."),
  component: VendorsPage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";

function VendorDialog({ initial, onClose }: { initial: Vendor | null; onClose: () => void }) {
  const s = useStore();
  const [f, setF] = useState<Vendor>(initial ?? {
    id: "", companyId: s.companyId, name: "", gstin: "", pan: "", defaultExpenseLedger: EXPENSE_LEDGERS[0], creditorLedger: "",
    parentGroup: "Indirect Expenses", transactions: 0, lastUsed: new Date().toISOString().slice(0, 10), status: "Active",
  });
  const [err, setErr] = useState<Record<string, string>>({});
  const set = (k: keyof Vendor, v: string) => setF((p) => ({ ...p, [k]: v }));
  const save = () => {
    const e: Record<string, string> = {};
    if (!f.name.trim()) e.name = "Vendor name is required";
    if (f.pan && !PAN_RE.test(f.pan)) e.pan = "Invalid PAN";
    if (f.gstin && !GSTIN_RE.test(f.gstin)) e.gstin = "Invalid GSTIN";
    setErr(e);
    if (Object.keys(e).length) return;
    const v = { ...f, creditorLedger: f.creditorLedger || f.name };
    s.setVendors((vs) => (f.id ? vs.map((x) => (x.id === f.id ? v : x)) : [{ ...v, id: `v-${Date.now()}` }, ...vs]));
    s.log({ module: "Vendor Master", action: f.id ? "Updated" : "Created", recordId: f.gstin || f.name, previous: "—", next: v.defaultExpenseLedger });
    toast.success(f.id ? "Vendor updated" : "Vendor added");
    onClose();
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader><DialogTitle>{f.id ? "Edit vendor" : "Add vendor"}</DialogTitle></DialogHeader>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Vendor Name" error={err.name}><Input value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label="Status"><select className={selectCls} value={f.status} onChange={(e) => set("status", e.target.value)}>{["Active", "Inactive", "Needs Mapping"].map((x) => <option key={x}>{x}</option>)}</select></Field>
          <Field label="GSTIN" error={err.gstin}><Input className="num" value={f.gstin} onChange={(e) => set("gstin", e.target.value.toUpperCase())} /></Field>
          <Field label="PAN" error={err.pan}><Input className="num" value={f.pan} onChange={(e) => set("pan", e.target.value.toUpperCase())} /></Field>
          <Field label="Default Expense Ledger"><select className={selectCls} value={f.defaultExpenseLedger} onChange={(e) => set("defaultExpenseLedger", e.target.value)}>{EXPENSE_LEDGERS.map((x) => <option key={x}>{x}</option>)}</select></Field>
          <Field label="Parent Group"><select className={selectCls} value={f.parentGroup} onChange={(e) => set("parentGroup", e.target.value)}>{PARENT_GROUPS.map((x) => <option key={x}>{x}</option>)}</select></Field>
          <div className="md:col-span-2"><Field label="Sundry Creditor Ledger"><Input value={f.creditorLedger} placeholder="Defaults to vendor name" onChange={(e) => set("creditorLedger", e.target.value)} /></Field></div>
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button><Button onClick={save}>Save vendor</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MappingDialog({ m, onClose }: { m: LedgerMapping; onClose: () => void }) {
  const s = useStore();
  const [f, setF] = useState(m);
  const save = (approve: boolean) => {
    const next = { ...f, ...(approve ? { status: "Approved" as const, approvedBy: "Ananya Rao", confidence: Math.max(f.confidence, 95) } : {}), updatedAt: new Date().toISOString().slice(0, 10) };
    s.setMappings((ms) => ms.map((x) => (x.id === m.id ? next : x)));
    s.log({ module: "Vendor Master", action: approve ? "Approved" : "Updated", recordId: m.vendorKey, previous: `Ledger: ${m.expenseLedger}`, next: `Ledger: ${f.expenseLedger}` });
    toast.success(approve ? "Mapping approved" : "Mapping saved");
    onClose();
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Configure mapping · {m.vendorName}</DialogTitle></DialogHeader>
        <div className="grid gap-3">
          <Field label="Description Keywords"><Input value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} /></Field>
          <Field label="Expense Ledger"><select className={selectCls} value={f.expenseLedger} onChange={(e) => setF({ ...f, expenseLedger: e.target.value })}>{EXPENSE_LEDGERS.map((x) => <option key={x}>{x}</option>)}</select></Field>
          <Field label="Creditor Ledger"><Input value={f.creditorLedger} onChange={(e) => setF({ ...f, creditorLedger: e.target.value })} /></Field>
          <Field label="Tax Ledger"><select className={selectCls} value={f.taxLedger} onChange={(e) => setF({ ...f, taxLedger: e.target.value })}>{["Input CGST / SGST", "Input IGST", "Input Cess"].map((x) => <option key={x}>{x}</option>)}</select></Field>
        </div>
        <DialogFooter><Button variant="outline" onClick={() => save(false)}>Save</Button><Button onClick={() => save(true)}><Check />Save & Approve</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function VendorsPage() {
  const s = useStore();
  const company = useCompany();
  const vendors = useScoped(s.vendors);
  const mappings = useScoped(s.mappings); // strictly scoped to the selected company
  const [status, setStatus] = useState("All");
  const [cat, setCat] = useState("All");
  const [editing, setEditing] = useState<Vendor | null | undefined>(undefined);
  const [history, setHistory] = useState<Vendor | null>(null);
  const [mapEdit, setMapEdit] = useState<LedgerMapping | null>(null);

  return (
    <>
      <PageHeader title="Vendor Master" description={`Vendors and ledger mappings for ${company.displayName}.`} actions={<Button onClick={() => setEditing(null)}><Plus />Add Vendor</Button>} />
      <DemoNotice>Mappings shown are scoped to {company.displayName} only. Backend authorization must enforce the same isolation.</DemoNotice>
      <Tabs defaultValue="vendors">
        <TabsList><TabsTrigger value="vendors">Vendors</TabsTrigger><TabsTrigger value="mappings">Historical Mappings</TabsTrigger></TabsList>
        <TabsContent value="vendors" className="mt-3">
          <DataTable
            rows={vendors.filter((v) => status === "All" || v.status === status)}
            rowKey={(r) => r.id}
            searchKeys={(r) => `${r.name} ${r.gstin} ${r.pan}`}
            searchPlaceholder="Search vendor, GSTIN, PAN…"
            toolbar={<FilterSelect label="Status" value={status} onChange={setStatus} options={["Active", "Inactive", "Needs Mapping"]} />}
            columns={[
              { key: "name", header: "Vendor Name", cell: (r) => <span className="font-medium">{r.name}</span> },
              { key: "gstin", header: "GSTIN", cell: (r) => <span className="num">{r.gstin || "—"}</span> },
              { key: "pan", header: "PAN", cell: (r) => <span className="num">{r.pan || "—"}</span> },
              { key: "defaultExpenseLedger", header: "Default Expense Ledger" },
              { key: "creditorLedger", header: "Sundry Creditor Ledger" },
              { key: "parentGroup", header: "Parent Group" },
              { key: "transactions", header: "Txns", align: "right", sortValue: (r) => r.transactions },
              { key: "lastUsed", header: "Last Used", cell: (r) => fmtDate(r.lastUsed) },
              { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
              {
                key: "a", header: "Actions", cell: (r) => (
                  <div className="flex gap-1">
                    <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Edit vendor" onClick={() => setEditing(r)}><Pencil /></Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Vendor history" onClick={() => setHistory(r)}><History /></Button>
                  </div>
                ),
              },
            ]}
          />
        </TabsContent>
        <TabsContent value="mappings" className="mt-3">
          <DataTable
            rows={mappings.filter((m) => cat === "All" || m.source === cat)}
            rowKey={(r) => r.id}
            searchKeys={(r) => `${r.vendorName} ${r.vendorKey} ${r.keywords} ${r.expenseLedger}`}
            toolbar={<FilterSelect label="Category" value={cat} onChange={setCat} options={["Exact GSTIN/PAN Match", "Description/Keyword Match", "Manual Review Required"]} />}
            columns={[
              { key: "vendorKey", header: "GSTIN/PAN", cell: (r) => <span className="num">{r.vendorKey}</span> },
              { key: "vendorName", header: "Vendor" },
              { key: "keywords", header: "Keywords" },
              { key: "expenseLedger", header: "Expense Ledger" },
              { key: "creditorLedger", header: "Creditor Ledger" },
              { key: "taxLedger", header: "Tax Ledger" },
              { key: "confidence", header: "Confidence", sortValue: (r) => r.confidence, cell: (r) => <ConfidenceBadge value={r.confidence} /> },
              { key: "source", header: "Source" },
              { key: "approvedBy", header: "Approved By" },
              { key: "updatedAt", header: "Updated", cell: (r) => fmtDate(r.updatedAt) },
              { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
              { key: "a", header: "", cell: (r) => <Button size="sm" variant="outline" className="h-7" onClick={() => setMapEdit(r)}>Configure</Button> },
            ]}
          />
        </TabsContent>
      </Tabs>

      {editing !== undefined && <VendorDialog initial={editing} onClose={() => setEditing(undefined)} />}
      {mapEdit && <MappingDialog m={mapEdit} onClose={() => setMapEdit(null)} />}
      <Sheet open={!!history} onOpenChange={(o) => !o && setHistory(null)}>
        <SheetContent className="w-full sm:max-w-md">
          <SheetHeader><SheetTitle>{history?.name}</SheetTitle></SheetHeader>
          {history && (
            <div className="mt-4 space-y-4 text-sm">
              <div><KV k="GSTIN" v={history.gstin} /><KV k="Transactions" v={history.transactions} /><KV k="Last used" v={fmtDate(history.lastUsed)} /></div>
              <h3 className="font-semibold">Mapping history</h3>
              {mappings.filter((m) => m.vendorKey === history.gstin).map((m) => (
                <div key={m.id} className="rounded border p-3">
                  <div className="flex justify-between"><b>{m.expenseLedger}</b><ConfidenceBadge value={m.confidence} /></div>
                  <div className="mt-1 text-xs text-muted-foreground">{m.source} · {m.approvedBy} · {fmtDate(m.updatedAt)}</div>
                </div>
              ))}
              {s.audit.filter((a) => a.companyId === s.companyId && a.module === "Vendor Master").slice(0, 4).map((a) => (
                <div key={a.id} className="text-xs text-muted-foreground">{a.timestamp} — {a.user} {a.action.toLowerCase()} {a.recordId}</div>
              ))}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
