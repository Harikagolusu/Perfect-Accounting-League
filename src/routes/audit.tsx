import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { PageHeader, StatusBadge, KV, InfoNotice } from "@/components/pal/common";
import { DataTable, FilterSelect } from "@/components/pal/data-table";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useStore } from "@/lib/store";
import { useAccountingAudit } from "@/lib/queries/accountingAudit";
import type { AuditEvent } from "@/lib/types";
import { seo } from "@/lib/seo";

export const Route = createFileRoute("/audit")({
  head: () => seo("Audit Trail", "Who changed what, when — across every PAL module."),
  component: AuditPage,
});

function AuditPage() {
  const s = useStore();
  const [company, setCompany] = useState(s.companyId);
  const [user, setUser] = useState("All");
  const [mod, setMod] = useState("All");
  const [action, setAction] = useState("All");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [open, setOpen] = useState<AuditEvent | null>(null);
  const { data, isError } = useAccountingAudit(company || s.companyId);
  const events = data ?? [];
  const name = (id: string) => s.companies.find((c) => c.id === id)?.displayName ?? id;
  const rows = events.filter(
    (e) =>
      (user === "All" || e.user === user) &&
      (mod === "All" || e.module === mod) &&
      (action === "All" || e.action === action) &&
      (!from || e.timestamp >= from) &&
      (!to || e.timestamp.slice(0, 10) <= to),
  );
  const userOptions = Array.from(new Set(events.map((e) => e.user)));

  return (
    <>
      <PageHeader title="Audit Trail" description="Chronological log of user and system actions." />
      <InfoNotice>
        {isError
          ? "The PAL backend could not be reached to load the audit trail."
          : "Events are recorded in PostgreSQL and persist across sessions and restarts."}
      </InfoNotice>
      <DataTable
        rows={rows}
        rowKey={(r) => r.id}
        pageSize={15}
        searchKeys={(r) => `${r.recordId} ${r.previous} ${r.next} ${r.user}`}
        onRowClick={setOpen}
        toolbar={
          <>
            <select
              aria-label="Company"
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
            >
              {s.companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.displayName}
                </option>
              ))}
            </select>
            <FilterSelect label="User" value={user} onChange={setUser} options={userOptions} />
            <FilterSelect
              label="Module"
              value={mod}
              onChange={setMod}
              options={Array.from(new Set(events.map((a) => a.module)))}
            />
            <FilterSelect
              label="Action"
              value={action}
              onChange={setAction}
              options={Array.from(new Set(events.map((a) => a.action)))}
            />
            <Input
              type="date"
              aria-label="From date"
              className="h-9 w-36"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
            <Input
              type="date"
              aria-label="To date"
              className="h-9 w-36"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </>
        }
        columns={[
          {
            key: "timestamp",
            header: "Timestamp",
            cell: (r) => <span className="num text-xs">{r.timestamp}</span>,
          },
          { key: "user", header: "User" },
          { key: "company", header: "Company", cell: (r) => name(r.companyId) },
          { key: "module", header: "Module" },
          { key: "action", header: "Action" },
          {
            key: "recordId",
            header: "Record ID",
            cell: (r) => <span className="num">{r.recordId}</span>,
          },
          { key: "previous", header: "Previous Value" },
          { key: "next", header: "New Value" },
          { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
        ]}
      />
      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full sm:max-w-md">
          <SheetHeader>
            <SheetTitle>Event {open?.id}</SheetTitle>
          </SheetHeader>
          {open && (
            <div className="mt-4">
              <KV k="Timestamp" v={open.timestamp} />
              <KV k="User" v={open.user} />
              <KV k="Company" v={name(open.companyId)} />
              <KV k="Module" v={open.module} />
              <KV k="Action" v={open.action} />
              <KV k="Record" v={open.recordId} />
              <KV k="Previous" v={open.previous} />
              <KV k="New" v={open.next} />
              <KV k="Status" v={<StatusBadge status={open.status} />} />
            </div>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
