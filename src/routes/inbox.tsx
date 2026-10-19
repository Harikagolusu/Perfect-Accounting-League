import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Eye, RotateCw, FileSearch } from "lucide-react";
import { DemoNotice, PageHeader, StatusBadge, Field, KV } from "@/components/pal/common";
import { DataTable, FilterSelect } from "@/components/pal/data-table";
import { FileUploadDropzone } from "@/components/pal/dropzone";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { useCompany, useScoped, useStore } from "@/lib/store";
import { FINANCIAL_YEARS, PERIODS } from "@/lib/mock-data";
import { fmtDate, fmtSize } from "@/lib/format";
import type { DocStatus, InboxDocument } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/inbox")({
  head: () => seo("Document Inbox", "Receive invoices and financial documents via email or manual upload."),
  component: InboxPage,
});

const STATUSES: DocStatus[] = ["Received", "Queued", "Processing", "Extracted", "Needs Review", "Approved", "Exported", "Failed"];
const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";

function InboxPage() {
  const s = useStore();
  const company = useCompany();
  const navigate = useNavigate();
  const docs = useScoped(s.documents);
  const [tab, setTab] = useState("all");
  const [status, setStatus] = useState("All");
  const [type, setType] = useState("All");
  const [preview, setPreview] = useState<InboxDocument | null>(null);
  const [staged, setStaged] = useState<File[]>([]);

  const update = (id: string, patch: Partial<InboxDocument>) => s.setDocuments((d) => d.map((x) => (x.id === id ? { ...x, ...patch } : x)));

  const simulate = (ids: string[]) => {
    ids.forEach((id) => update(id, { status: "Processing" }));
    setTimeout(() => {
      ids.forEach((id, i) => update(id, { status: i % 4 === 3 ? "Needs Review" : "Extracted" }));
      toast.success("Demo processing complete", { description: `${ids.length} document(s) moved to Extracted / Needs Review. No real OCR was run.` });
    }, 1800);
  };

  const submit = () => {
    const now = new Date().toISOString().slice(0, 10);
    const newDocs: InboxDocument[] = staged.map((f, i) => ({
      id: `D-UP-${Date.now()}-${i}`, companyId: s.companyId, name: f.name, source: "Manual Upload", receivedAt: now,
      fileType: f.name.split(".").pop()?.toUpperCase() ?? "FILE", status: "Queued", reviewer: "Unassigned", size: f.size,
    }));
    s.setDocuments((d) => [...newDocs, ...d]);
    toast.success(`${staged.length} document(s) uploaded to ${company.displayName}`);
    setStaged([]);
    simulate(newDocs.map((d) => d.id));
  };

  const rows = docs.filter((d) => {
    if (tab === "email" && d.source !== "Email") return false;
    if (tab === "manual" && d.source !== "Manual Upload") return false;
    if (tab === "review" && d.status !== "Needs Review") return false;
    if (tab === "failed" && d.status !== "Failed") return false;
    return (status === "All" || d.status === status) && (type === "All" || d.fileType === type);
  });

  return (
    <>
      <PageHeader title="Document Inbox" description="Invoices and financial documents received by email or manual upload." />
      <DemoNotice>Email intake and OCR are not connected yet. Processing states shown here are simulated.</DemoNotice>

      <div className="mb-4 grid gap-4 rounded-md border bg-card p-4 lg:grid-cols-[1fr_320px]">
        <div>
          <FileUploadDropzone onFiles={(f) => setStaged((p) => [...p, ...f])} />
          {staged.length > 0 && (
            <ul className="mt-3 divide-y rounded border text-sm">
              {staged.map((f, i) => (
                <li key={i} className="flex items-center justify-between px-3 py-1.5">
                  <span className="truncate">{f.name}</span>
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">{fmtSize(f.size)}
                    <button className="text-destructive hover:underline" onClick={() => setStaged((p) => p.filter((_, j) => j !== i))}>Remove</button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex flex-col gap-3">
          <Field label="Company"><select className={selectCls} value={s.companyId} onChange={(e) => s.setCompanyId(e.target.value)}>{s.companies.map((c) => <option key={c.id} value={c.id}>{c.displayName}</option>)}</select></Field>
          <Field label="Financial Year"><select className={selectCls} value={s.financialYear} onChange={(e) => s.setFinancialYear(e.target.value)}>{FINANCIAL_YEARS.map((x) => <option key={x}>{x}</option>)}</select></Field>
          <Field label="Reporting Period"><select className={selectCls} value={s.period} onChange={(e) => s.setPeriod(e.target.value)}>{PERIODS.map((x) => <option key={x}>{x}</option>)}</select></Field>
          <Button disabled={!staged.length} onClick={submit}>Upload {staged.length || ""} file{staged.length === 1 ? "" : "s"}</Button>
        </div>
      </div>

      <Tabs value={tab} onValueChange={setTab} className="mb-3">
        <TabsList className="flex-wrap">
          <TabsTrigger value="all">All Documents</TabsTrigger>
          <TabsTrigger value="email">Email Attachments</TabsTrigger>
          <TabsTrigger value="manual">Manual Uploads</TabsTrigger>
          <TabsTrigger value="review">Needs Review</TabsTrigger>
          <TabsTrigger value="failed">Failed Documents</TabsTrigger>
        </TabsList>
      </Tabs>

      <DataTable
        rows={rows}
        rowKey={(r) => r.id}
        searchKeys={(r) => `${r.name} ${r.reviewer} ${r.id}`}
        selectable
        bulkActions={(ids, clear) => <Button size="sm" variant="outline" className="h-7" onClick={() => { simulate(ids); clear(); }}>Process selected</Button>}
        toolbar={<>
          <FilterSelect label="Status" value={status} onChange={setStatus} options={STATUSES} />
          <FilterSelect label="Type" value={type} onChange={setType} options={["PDF", "JPEG", "PNG", "XLSX", "CSV"]} />
        </>}
        columns={[
          { key: "name", header: "Document Name", cell: (r) => <span className="font-medium">{r.name}</span> },
          { key: "source", header: "Source" },
          { key: "company", header: "Company", cell: () => company.displayName },
          { key: "receivedAt", header: "Received", cell: (r) => fmtDate(r.receivedAt) },
          { key: "fileType", header: "File Type" },
          { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
          { key: "reviewer", header: "Reviewer" },
          {
            key: "actions", header: "Actions", cell: (r) => (
              <div className="flex gap-1">
                <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Preview" onClick={() => setPreview(r)}><Eye /></Button>
                {(r.status === "Extracted" || r.status === "Needs Review" || r.status === "Approved") && (
                  <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Open in invoice workspace" onClick={() => navigate({ to: "/invoices", search: { doc: r.id } })}><FileSearch /></Button>
                )}
                {r.status === "Failed" && <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Retry" onClick={() => simulate([r.id])}><RotateCw /></Button>}
              </div>
            ),
          },
        ]}
      />

      <Dialog open={!!preview} onOpenChange={(o) => !o && setPreview(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader><DialogTitle>{preview?.name}</DialogTitle></DialogHeader>
          {preview && (
            <>
              <div className="flex h-56 items-center justify-center rounded border bg-muted text-sm text-muted-foreground">
                Preview placeholder — original file is stored by the backend
              </div>
              <div>
                <KV k="Document ID" v={preview.id} />
                <KV k="Source" v={preview.source} />
                <KV k="Received" v={fmtDate(preview.receivedAt)} />
                <KV k="Size" v={fmtSize(preview.size)} />
                <KV k="Status" v={<StatusBadge status={preview.status} />} />
              </div>
            </>
          )}
          <DialogFooter>
            {preview?.status === "Failed" && <Button variant="outline" onClick={() => { simulate([preview.id]); setPreview(null); }}><RotateCw />Retry</Button>}
            <Button onClick={() => setPreview(null)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
