import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Check, X, RotateCcw, ExternalLink } from "lucide-react";
import { PageHeader, StatusBadge, InfoNotice, KV, Field, EmptyState } from "@/components/pal/common";
import { DataTable, FilterSelect } from "@/components/pal/data-table";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useCompany, useScoped, useStore } from "@/lib/store";
import { fmtDate } from "@/lib/format";
import type { ReviewTask } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/review")({
  head: () => seo("Review Queue", "Centralized queue for invoice, mapping, bank, GST and TDS reviews."),
  component: ReviewPage,
});

const TYPES = ["Invoice Data Review", "Ledger Mapping Review", "Low Confidence Extraction", "Bank Transaction Review", "GST Mismatch Review", "TDS Exception Review"];
const LINK: Record<string, string> = {
  "Invoice Data Review": "/invoices", "Ledger Mapping Review": "/vendors", "Low Confidence Extraction": "/invoices",
  "Bank Transaction Review": "/bank", "GST Mismatch Review": "/reconciliation", "TDS Exception Review": "/tds",
};

function ReviewPage() {
  const s = useStore();
  const company = useCompany();
  const navigate = useNavigate();
  const tasks = useScoped(s.reviews);
  const [type, setType] = useState("All");
  const [st, setSt] = useState("Open");
  const [pr, setPr] = useState("All");
  const [openId, setOpenId] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const open = tasks.find((t) => t.id === openId) ?? null;
  const canAct = s.role !== "Viewer";
  const assignees = Array.from(new Set(s.reviews.map((t) => t.assignedTo).filter(Boolean)));

  if (!company) {
    return (
      <>
        <PageHeader title="Review Queue" description="Items awaiting accountant review." />
        <div className="rounded-md border bg-card p-4">
          <EmptyState title="No company selected" hint="Create a company before reviewing records." />
        </div>
      </>
    );
  }

  const update = (ids: string[], patch: Partial<ReviewTask>, msg: string) => {
    if (!canAct) return toast.error("Your role does not permit review actions.");
    s.setReviews((all) => all.map((t) => (ids.includes(t.id) ? { ...t, ...patch } : t)));
    ids.forEach((id) => s.log({ module: "Review Queue", action: patch.status ?? "Updated", recordId: id, previous: "Open", next: patch.status ?? patch.assignedTo ?? "" }));
    toast.success(msg);
  };

  return (
    <>
      <PageHeader title="Review Queue" description={`Items awaiting accountant review for ${company.displayName}.`} />
      {!canAct && <InfoNotice>You are previewing as Viewer — approve/reject actions are disabled.</InfoNotice>}
      <DataTable
        rows={tasks.filter((t) => (type === "All" || t.type === type) && (st === "All" || t.status === st) && (pr === "All" || t.priority === pr))}
        rowKey={(r) => r.id}
        searchKeys={(r) => `${r.id} ${r.description} ${r.assignedTo}`}
        selectable
        onRowClick={(r) => setOpenId(r.id)}
        bulkActions={(ids, clear) => <>
          <Button size="sm" variant="outline" className="h-7" onClick={() => { update(ids, { status: "Approved" }, `${ids.length} approved`); clear(); }}>Approve</Button>
          <Button size="sm" variant="outline" className="h-7" onClick={() => { update(ids, { status: "Rejected" }, `${ids.length} rejected`); clear(); }}>Reject</Button>
          <select aria-label="Assign selected" className="h-7 rounded border border-input bg-background text-xs" defaultValue="" onChange={(e) => { if (e.target.value) { update(ids, { assignedTo: e.target.value }, `Assigned to ${e.target.value}`); clear(); } }}>
            <option value="">Assign…</option>{assignees.map((u) => <option key={u}>{u}</option>)}
          </select>
        </>}
        toolbar={<>
          <FilterSelect label="Type" value={type} onChange={setType} options={TYPES} />
          <FilterSelect label="Status" value={st} onChange={setSt} options={["Open", "Approved", "Rejected", "Correction Requested"]} />
          <FilterSelect label="Priority" value={pr} onChange={setPr} options={["High", "Medium", "Low"]} />
        </>}
        columns={[
          { key: "id", header: "Record ID", cell: (r) => <span className="num">{r.id}</span> },
          { key: "company", header: "Company", cell: () => company.displayName },
          { key: "type", header: "Review Type" },
          { key: "description", header: "Description" },
          { key: "assignedTo", header: "Assigned To" },
          { key: "createdAt", header: "Created", cell: (r) => fmtDate(r.createdAt) },
          { key: "priority", header: "Priority", sortValue: (r) => ({ High: 0, Medium: 1, Low: 2 })[r.priority], cell: (r) => <StatusBadge status={r.priority} /> },
          { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
        ]}
      />
      <Sheet open={!!open} onOpenChange={(o) => !o && setOpenId(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-md">
          <SheetHeader><SheetTitle>{open?.id}</SheetTitle></SheetHeader>
          {open && (
            <div className="mt-4 space-y-4">
              <div><KV k="Type" v={open.type} /><KV k="Description" v={open.description} /><KV k="Priority" v={<StatusBadge status={open.priority} />} /><KV k="Status" v={<StatusBadge status={open.status} />} /></div>
              <Field label="Assign reviewer">
                <select className="h-9 rounded-md border border-input bg-background px-2 text-sm" value={open.assignedTo} onChange={(e) => update([open.id], { assignedTo: e.target.value }, `Assigned to ${e.target.value}`)}>
                  {["Unassigned", ...assignees].map((u) => <option key={u}>{u}</option>)}
                </select>
              </Field>
              <Button variant="outline" className="w-full" onClick={() => navigate({ to: LINK[open.type] })}><ExternalLink />Open record</Button>
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => update([open.id], { status: "Approved" }, "Approved")}><Check />Approve</Button>
                <Button variant="destructive" onClick={() => update([open.id], { status: "Rejected" }, "Rejected")}><X />Reject</Button>
                <Button variant="outline" onClick={() => update([open.id], { status: "Correction Requested" }, "Correction requested")}><RotateCcw />Request correction</Button>
              </div>
              <div>
                <h3 className="mb-2 text-sm font-semibold">Comments</h3>
                {open.comments.map((c, i) => <p key={i} className="mb-1 rounded bg-muted p-2 text-xs">{c}</p>)}
                <Textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Add a comment…" />
                <Button size="sm" className="mt-2" disabled={!comment.trim()} onClick={() => { s.setReviews((all) => all.map((t) => (t.id === open.id ? { ...t, comments: [...t.comments, `Local user: ${comment}`] } : t))); setComment(""); toast.success("Comment added"); }}>Add comment</Button>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
