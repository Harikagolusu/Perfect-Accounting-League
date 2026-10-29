import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Plus, Pencil, Eye, RefreshCw, Trash2 } from "lucide-react";
import { PageHeader, StatusBadge, Field, EmptyState } from "@/components/pal/common";
import { DataTable, FilterSelect } from "@/components/pal/data-table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FINANCIAL_YEARS } from "@/lib/constants";
import { EMAIL_RE, GSTIN_RE, PAN_RE } from "@/lib/format";
import {
  useCompanies,
  useCreateCompany,
  useDeactivateCompany,
  useUpdateCompany,
} from "@/lib/queries/companies";
import { useTallyCompanySync } from "@/lib/queries/tally";
import type { Company } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/companies/")({
  head: () => seo("Companies & Clients", "Manage client companies, GSTIN and PAN."),
  component: CompaniesPage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";
const blank: Company = {
  id: "",
  legalName: "",
  displayName: "",
  pan: "",
  gstin: "",
  address: "",
  state: "",
  contactPerson: "",
  email: "",
  phone: "",
  financialYear: FINANCIAL_YEARS[0],
  software: "None",
  assignedUsers: [],
  status: "Onboarding",
  cgstLedger: "",
  sgstLedger: "",
  igstLedger: "",
  cessLedger: "",
  roundOffLedger: "",
  salesLedger: "",
};

export function CompanyDialog({
  initial,
  open,
  onOpenChange,
}: {
  initial: Company | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const [f, setF] = useState<Company>(initial ?? blank);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const createCompany = useCreateCompany();
  const updateCompany = useUpdateCompany();
  const set = (k: keyof Company, v: string) => setF((p) => ({ ...p, [k]: v }));
  const saving = createCompany.isPending || updateCompany.isPending;

  const save = async () => {
    const e: Record<string, string> = {};
    if (!f.legalName.trim()) e.legalName = "Legal name is required";
    if (!f.displayName.trim()) e.displayName = "Display name is required";
    if (!PAN_RE.test(f.pan)) e.pan = "Enter a valid PAN (e.g. AAJCS4821K)";
    if (!GSTIN_RE.test(f.gstin)) e.gstin = "Enter a valid 15-character GSTIN";
    else if (PAN_RE.test(f.pan) && f.gstin.slice(2, 12) !== f.pan)
      e.gstin = "GSTIN must contain the company PAN";
    if (!EMAIL_RE.test(f.email)) e.email = "Enter a valid email";
    setErrors(e);
    if (Object.keys(e).length) return;

    const wasExisting = Boolean(f.id);
    try {
      if (wasExisting) await updateCompany.mutateAsync(f);
      else await createCompany.mutateAsync({ ...f, id: "" });
      toast.success(wasExisting ? "Company updated" : "Company added");
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save company");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{f.id ? "Edit company" : "Add company"}</DialogTitle>
        </DialogHeader>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Legal Company Name" error={errors.legalName}>
            <Input value={f.legalName} onChange={(e) => set("legalName", e.target.value)} />
          </Field>
          <Field label="Display Name" error={errors.displayName}>
            <Input value={f.displayName} onChange={(e) => set("displayName", e.target.value)} />
          </Field>
          <Field label="PAN" error={errors.pan}>
            <Input
              className="num uppercase"
              maxLength={10}
              value={f.pan}
              onChange={(e) => set("pan", e.target.value.toUpperCase())}
            />
          </Field>
          <Field label="GSTIN" error={errors.gstin}>
            <Input
              className="num uppercase"
              maxLength={15}
              value={f.gstin}
              onChange={(e) => set("gstin", e.target.value.toUpperCase())}
            />
          </Field>
          <div className="md:col-span-2">
            <Field label="Registered Address">
              <Textarea
                rows={2}
                value={f.address}
                onChange={(e) => set("address", e.target.value)}
              />
            </Field>
          </div>
          <Field label="Email" error={errors.email}>
            <Input type="email" value={f.email} onChange={(e) => set("email", e.target.value)} />
          </Field>
          <Field label="Phone">
            <Input value={f.phone} onChange={(e) => set("phone", e.target.value)} />
          </Field>
          <Field label="Financial Year">
            <select
              className={selectCls}
              value={f.financialYear}
              onChange={(e) => set("financialYear", e.target.value)}
            >
              {FINANCIAL_YEARS.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </Field>
          <Field label="Company Status">
            <select
              className={selectCls}
              value={f.status}
              onChange={(e) => set("status", e.target.value)}
            >
              {["Active", "Onboarding", "Inactive"].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save company"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CompaniesPage() {
  const [status, setStatus] = useState("All");
  const [editing, setEditing] = useState<Company | null>(null);
  const [open, setOpen] = useState(false);
  // include_inactive so every status the filter offers is present.
  const { data, isPending, isError, error, refetch } = useCompanies(true);
  const deactivate = useDeactivateCompany();
  const tallyCompanySync = useTallyCompanySync();
  // "All" means every live company. Archived (INACTIVE) companies are only
  // shown when the user explicitly selects the Inactive filter, so deleting a
  // company removes it from the default list instead of leaving a dead row.
  const rows = (data ?? []).filter((c) => {
    if (status === "Inactive") return c.status === "Inactive";
    if (c.status === "Inactive") return false;
    return status === "All" || c.status === status;
  });

  const onSyncCompanies = async () => {
    try {
      const result = await tallyCompanySync.mutateAsync();
      if (!result.success) {
        toast.error(result.error_message ?? "Could not sync companies from TallyPrime");
        return;
      }
      toast.success(`Synced ${result.companies_fetched} Tally companies`, {
        description: `${result.companies_created} created, ${result.companies_updated} updated, ${result.companies_unchanged} unchanged`,
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not sync companies from TallyPrime");
    }
  };

  const onDeactivate = async (row: Company) => {
    try {
      await deactivate.mutateAsync(row.id);
      toast.success(`${row.displayName} removed from active companies`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not deactivate company");
    }
  };

  return (
    <>
      <PageHeader
        title="Companies & Clients"
        description="Client entities managed by your firm."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={onSyncCompanies}
              disabled={tallyCompanySync.isPending}
            >
              <RefreshCw className={tallyCompanySync.isPending ? "animate-spin" : undefined} />
              {tallyCompanySync.isPending ? "Syncing…" : "Sync from Tally"}
            </Button>
            <Button
              onClick={() => {
                setEditing(null);
                setOpen(true);
              }}
            >
              <Plus />
              Add Company
            </Button>
          </div>
        }
      />
      {isPending ? (
        <div className="rounded-md border bg-card p-4">
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">Loading companies…</p>
        </div>
      ) : isError ? (
        <div className="rounded-md border border-destructive/40 bg-card p-6">
          <EmptyState
            title="Could not load companies"
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
          rows={rows}
          rowKey={(r) => r.id}
          searchKeys={(r) => `${r.legalName} ${r.displayName} ${r.gstin} ${r.pan}`}
          searchPlaceholder="Search name, GSTIN, PAN…"
          toolbar={
            <FilterSelect
              label="Status"
              value={status}
              onChange={setStatus}
              options={["Active", "Onboarding", "Inactive"]}
            />
          }
          columns={[
            {
              key: "legalName",
              header: "Company Name",
              cell: (r) => (
                <div>
                  <div className="font-medium">{r.displayName}</div>
                  <div className="text-xs text-muted-foreground">{r.legalName}</div>
                </div>
              ),
            },
            { key: "gstin", header: "GSTIN", cell: (r) => <span className="num">{r.gstin}</span> },
            { key: "pan", header: "PAN", cell: (r) => <span className="num">{r.pan}</span> },
            { key: "financialYear", header: "Financial Year" },
            { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
            {
              key: "actions",
              header: "Actions",
              cell: (r) => (
                <div className="flex gap-1">
                  <Button asChild variant="ghost" size="icon" className="h-7 w-7" aria-label="View">
                    <Link to="/companies/$id" params={{ id: r.id }}>
                      <Eye />
                    </Link>
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    aria-label="Edit"
                    onClick={() => {
                      setEditing(r);
                      setOpen(true);
                    }}
                  >
                    <Pencil />
                  </Button>
                  {r.status !== "Inactive" && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label="Deactivate"
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
      {open && (
        <CompanyDialog
          key={editing?.id ?? "new"}
          initial={editing}
          open={open}
          onOpenChange={setOpen}
        />
      )}
    </>
  );
}
