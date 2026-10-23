import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Plus, Pencil, History, Trash2 } from "lucide-react";
import { PageHeader, StatusBadge, Field, KV, EmptyState } from "@/components/pal/common";
import { DataTable, FilterSelect } from "@/components/pal/data-table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useCompany, useStore } from "@/lib/store";
import { fmtDate, GSTIN_RE, PAN_RE } from "@/lib/format";
import {
  useCreateVendor,
  useDeactivateVendor,
  useUpdateVendor,
  useVendors,
} from "@/lib/queries/vendors";
import type { Vendor } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/vendors")({
  head: () => seo("Vendor Master", "Vendors, default ledgers and historical grouping per company."),
  component: VendorsPage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";

/** Vendor transactions and last-used are derived from invoices. */
const lastUsed = (vendor: Vendor) => (vendor.lastUsed ? fmtDate(vendor.lastUsed) : "—");

function VendorDialog({ initial, onClose }: { initial: Vendor | null; onClose: () => void }) {
  const s = useStore();
  const company = useCompany();
  const createVendor = useCreateVendor(company?.id);
  const updateVendor = useUpdateVendor(company?.id);
  const [f, setF] = useState<Vendor>(
    initial ?? {
      id: "",
      companyId: company?.id ?? "",
      name: "",
      gstin: "",
      pan: "",
      defaultExpenseLedger: "",
      creditorLedger: "",
      parentGroup: "",
      transactions: 0,
      lastUsed: "",
      status: "Active",
    },
  );
  const [err, setErr] = useState<Record<string, string>>({});
  const saving = createVendor.isPending || updateVendor.isPending;
  const set = (k: keyof Vendor, v: string) => setF((p) => ({ ...p, [k]: v }));

  const save = async () => {
    const e: Record<string, string> = {};
    if (!f.name.trim()) e.name = "Vendor name is required";
    if (f.pan && !PAN_RE.test(f.pan)) e.pan = "Invalid PAN";
    if (f.gstin && !GSTIN_RE.test(f.gstin)) e.gstin = "Invalid GSTIN";
    setErr(e);
    if (Object.keys(e).length) return;

    const v = { ...f, creditorLedger: f.creditorLedger || f.name };
    const wasExisting = Boolean(f.id);
    try {
      if (wasExisting) await updateVendor.mutateAsync(v);
      else await createVendor.mutateAsync({ ...v, id: "" });
      s.log({
        module: "Vendor Master",
        action: wasExisting ? "Updated" : "Created",
        recordId: f.gstin || f.name,
        previous: "—",
        next: v.defaultExpenseLedger || v.name,
      });
      toast.success(wasExisting ? "Vendor updated" : "Vendor added");
      onClose();
    } catch (e2) {
      toast.error(e2 instanceof Error ? e2.message : "Could not save vendor");
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{f.id ? "Edit vendor" : "Add vendor"}</DialogTitle>
        </DialogHeader>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Vendor Name" error={err.name}>
            <Input value={f.name} onChange={(e) => set("name", e.target.value)} />
          </Field>
          <Field label="Status">
            <select
              className={selectCls}
              value={f.status}
              onChange={(e) => set("status", e.target.value)}
            >
              {["Active", "Inactive", "Needs Mapping"].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </Field>
          <Field label="GSTIN" error={err.gstin}>
            <Input
              className="num"
              value={f.gstin}
              onChange={(e) => set("gstin", e.target.value.toUpperCase())}
            />
          </Field>
          <Field label="PAN" error={err.pan}>
            <Input
              className="num"
              value={f.pan}
              onChange={(e) => set("pan", e.target.value.toUpperCase())}
            />
          </Field>
          <Field label="Default Expense Ledger">
            <Input
              value={f.defaultExpenseLedger}
              placeholder="Ledger name from your books"
              onChange={(e) => set("defaultExpenseLedger", e.target.value)}
            />
          </Field>
          <Field label="Parent Group">
            <Input
              value={f.parentGroup}
              placeholder="e.g. Sundry Creditors"
              onChange={(e) => set("parentGroup", e.target.value)}
            />
          </Field>
          <div className="md:col-span-2">
            <Field label="Sundry Creditor Ledger">
              <Input
                value={f.creditorLedger}
                placeholder="Defaults to vendor name"
                onChange={(e) => set("creditorLedger", e.target.value)}
              />
            </Field>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save vendor"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function VendorsPage() {
  const s = useStore();
  const company = useCompany();
  const companyId = company?.id;
  const { data, isPending, isError, error, refetch } = useVendors(companyId);
  const deactivate = useDeactivateVendor(companyId);
  const [status, setStatus] = useState("All");
  const [editing, setEditing] = useState<Vendor | null | undefined>(undefined);
  const [history, setHistory] = useState<Vendor | null>(null);

  const vendors = (data ?? []).filter((v) => status === "All" || v.status === status);

  const onDeactivate = async (row: Vendor) => {
    try {
      await deactivate.mutateAsync(row.id);
      toast.success(`${row.name} deactivated`);
      s.log({
        module: "Vendor Master",
        action: "Deactivated",
        recordId: row.gstin || row.name,
        previous: row.status,
        next: "Inactive",
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not deactivate vendor");
    }
  };

  return (
    <>
      <PageHeader
        title="Vendor Master"
        description={`Vendors and ledger grouping for ${company?.displayName ?? "the selected company"}.`}
        actions={
          <Button onClick={() => setEditing(null)} disabled={!companyId}>
            <Plus />
            Add Vendor
          </Button>
        }
      />
      {!companyId ? (
        <div className="rounded-md border bg-card p-4">
          <EmptyState
            title="No company selected"
            hint="Create a company first — every vendor belongs to one."
          />
        </div>
      ) : isPending ? (
        <div className="rounded-md border bg-card p-4">
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">Loading vendors…</p>
        </div>
      ) : isError ? (
        <div className="rounded-md border border-destructive/40 bg-card p-6">
          <EmptyState
            title="Could not load vendors"
            hint={error instanceof Error ? error.message : "The backend is unreachable."}
          />
          <div className="flex justify-center">
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Try again
            </Button>
          </div>
        </div>
      ) : (
        <DataTable
          rows={vendors}
          rowKey={(r) => r.id}
          searchKeys={(r) => `${r.name} ${r.gstin} ${r.pan}`}
          searchPlaceholder="Search vendor, GSTIN, PAN…"
          toolbar={
            <FilterSelect
              label="Status"
              value={status}
              onChange={setStatus}
              options={["Active", "Inactive", "Needs Mapping"]}
            />
          }
          columns={[
            {
              key: "name",
              header: "Vendor Name",
              cell: (r) => <span className="font-medium">{r.name}</span>,
            },
            {
              key: "gstin",
              header: "GSTIN",
              cell: (r) => <span className="num">{r.gstin || "—"}</span>,
            },
            {
              key: "pan",
              header: "PAN",
              cell: (r) => <span className="num">{r.pan || "—"}</span>,
            },
            { key: "defaultExpenseLedger", header: "Default Expense Ledger" },
            { key: "creditorLedger", header: "Sundry Creditor Ledger" },
            { key: "parentGroup", header: "Parent Group" },
            {
              key: "transactions",
              header: "Txns",
              align: "right",
              sortValue: (r) => r.transactions,
            },
            { key: "lastUsed", header: "Last Used", cell: (r) => lastUsed(r) },
            {
              key: "status",
              header: "Status",
              cell: (r) => <StatusBadge status={r.status} />,
            },
            {
              key: "a",
              header: "Actions",
              cell: (r) => (
                <div className="flex gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    aria-label="Edit vendor"
                    onClick={() => setEditing(r)}
                  >
                    <Pencil />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    aria-label="Vendor history"
                    onClick={() => setHistory(r)}
                  >
                    <History />
                  </Button>
                  {r.status !== "Inactive" && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label="Deactivate vendor"
                      disabled={deactivate.isPending}
                      onClick={() => onDeactivate(r)}
                    >
                      <Trash2 />
                    </Button>
                  )}
                </div>
              ),
            },
          ]}
        />
      )}
      {editing !== undefined && (
        <VendorDialog initial={editing} onClose={() => setEditing(undefined)} />
      )}
      <Sheet open={!!history} onOpenChange={(o) => !o && setHistory(null)}>
        <SheetContent className="w-full sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{history?.name}</SheetTitle>
          </SheetHeader>
          {history && (
            <div className="mt-4 space-y-4 text-sm">
              <div>
                <KV k="GSTIN" v={history.gstin || "—"} />
                <KV k="PAN" v={history.pan || "—"} />
                <KV k="Default expense ledger" v={history.defaultExpenseLedger || "—"} />
                <KV k="Sundry creditor ledger" v={history.creditorLedger || "—"} />
                <KV k="Parent group" v={history.parentGroup || "—"} />
                <KV k="Status" v={<StatusBadge status={history.status} />} />
                <KV k="Transactions" v={history.transactions} />
                <KV k="Last used" v={lastUsed(history)} />
              </div>
              <h3 className="font-semibold">Session activity</h3>
              {s.audit
                .filter((a) => a.companyId === s.companyId && a.module === "Vendor Master")
                .slice(0, 4)
                .map((a) => (
                  <div key={a.id} className="text-xs text-muted-foreground">
                    {a.timestamp} — {a.user} {a.action.toLowerCase()} {a.recordId}
                  </div>
                ))}
              {!s.audit.filter((a) => a.companyId === s.companyId && a.module === "Vendor Master")
                .length && <EmptyState title="No activity in this session" />}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
