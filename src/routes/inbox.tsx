import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Eye, Trash2 } from "lucide-react";
import {
  InfoNotice,
  PageHeader,
  StatusBadge,
  Field,
  KV,
  EmptyState,
} from "@/components/pal/common";
import { DataTable, FilterSelect } from "@/components/pal/data-table";
import { FileUploadDropzone } from "@/components/pal/dropzone";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { useCompany, useStore } from "@/lib/store";
import { FINANCIAL_YEARS, PERIODS } from "@/lib/constants";
import { fmtDate, fmtSize } from "@/lib/format";
import { FILE_TYPE_OPTIONS } from "@/lib/api/documents";
import { useDeleteDocument, useDocuments, useUploadDocuments } from "@/lib/queries/documents";
import type { DocStatus, InboxDocument } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/inbox")({
  head: () =>
    seo("Document Inbox", "Receive invoices and financial documents via email or manual upload."),
  component: InboxPage,
});

const STATUSES: DocStatus[] = ["Uploaded", "Processing", "Extracted", "Failed"];
const ACCEPT = ".pdf,.jpg,.jpeg,.png,.xls,.xlsx,.docx";
const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";

function InboxPage() {
  const s = useStore();
  const company = useCompany();
  const companyId = company?.id;
  const { data, isPending, isError, error, refetch } = useDocuments(companyId);
  const upload = useUploadDocuments(companyId);
  const remove = useDeleteDocument(companyId);
  const [tab, setTab] = useState("all");
  const [status, setStatus] = useState("All");
  const [type, setType] = useState("All");
  const [preview, setPreview] = useState<InboxDocument | null>(null);
  const [staged, setStaged] = useState<File[]>([]);

  const docs = data ?? [];

  const submit = async () => {
    if (!staged.length) return;
    const files = staged;
    // Clear immediately so the button cannot be double-submitted while the
    // upload is in flight.
    setStaged([]);
    try {
      const saved = await upload.mutateAsync(files);
      toast.success(
        `${saved.length} document${saved.length === 1 ? "" : "s"} uploaded to ${company?.displayName ?? "the inbox"}`,
      );
    } catch (e) {
      // Put the files back so the user can retry or adjust them.
      setStaged((prev) => [...files, ...prev]);
      toast.error(e instanceof Error ? e.message : "Upload failed");
    }
  };

  const onDelete = async (doc: InboxDocument) => {
    try {
      await remove.mutateAsync(doc.id);
      toast.success(`${doc.name} deleted`);
      if (preview?.id === doc.id) setPreview(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete document");
    }
  };

  const rows = docs.filter((d) => {
    if (tab === "email" && d.source !== "Email") return false;
    if (tab === "review" && d.status !== "Needs Review") return false;
    if (tab === "failed" && d.status !== "Failed") return false;
    return (status === "All" || d.status === status) && (type === "All" || d.fileType === type);
  });

  return (
    <>
      <PageHeader
        title="Document Inbox"
        description="Invoices and financial documents received by email or manual upload."
      />
      <InfoNotice>
        Files are validated and stored locally; nothing is processed automatically yet, so every
        document stays <b>Unknown</b> until a later step classifies it. Email intake is not
        connected.
      </InfoNotice>

      <div className="mb-4 grid gap-4 rounded-md border bg-card p-4 lg:grid-cols-[1fr_320px]">
        <div>
          <FileUploadDropzone
            accept={ACCEPT}
            hint="PDF, JPEG, PNG, XLSX, XLS, DOCX"
            onFiles={(f) => setStaged((p) => [...p, ...f])}
          />
          {staged.length > 0 && (
            <ul className="mt-3 divide-y rounded border text-sm">
              {staged.map((f, i) => (
                <li
                  key={`${f.name}-${i}`}
                  className="flex items-center justify-between px-3 py-1.5"
                >
                  <span className="truncate">{f.name}</span>
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">
                    {fmtSize(f.size)}
                    <button
                      className="text-destructive hover:underline"
                      onClick={() => setStaged((p) => p.filter((_, j) => j !== i))}
                    >
                      Remove
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex flex-col gap-3">
          <Field label="Company">
            <select
              className={selectCls}
              value={s.companyId}
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
          <Field label="Reporting Period">
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
          <Button disabled={!staged.length || upload.isPending || !companyId} onClick={submit}>
            {upload.isPending
              ? "Uploading…"
              : `Upload ${staged.length || ""} file${staged.length === 1 ? "" : "s"}`}
          </Button>
        </div>
      </div>

      {!companyId ? (
        <div className="rounded-md border bg-card p-4">
          <EmptyState
            title="No company selected"
            hint="Create a company first — every document belongs to one."
          />
        </div>
      ) : isPending ? (
        <div className="rounded-md border bg-card p-4">
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">Loading documents…</p>
        </div>
      ) : isError ? (
        <div className="rounded-md border border-destructive/40 bg-card p-6">
          <EmptyState
            title="Could not load documents"
            hint={error instanceof Error ? error.message : "The backend is unreachable."}
          />
          <div className="flex justify-center">
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Try again
            </Button>
          </div>
        </div>
      ) : (
        <>
          <Tabs value={tab} onValueChange={setTab} className="mb-3">
            <TabsList className="flex-wrap">
              <TabsTrigger value="all">All Documents</TabsTrigger>
              <TabsTrigger value="email">Email Attachments</TabsTrigger>
              <TabsTrigger value="review">Needs Review</TabsTrigger>
              <TabsTrigger value="failed">Failed Documents</TabsTrigger>
            </TabsList>
          </Tabs>

          <DataTable
            rows={rows}
            rowKey={(r) => r.id}
            searchKeys={(r) => `${r.name} ${r.reviewer} ${r.id}`}
            toolbar={
              <>
                <FilterSelect
                  label="Status"
                  value={status}
                  onChange={setStatus}
                  options={STATUSES}
                />
                <FilterSelect
                  label="Type"
                  value={type}
                  onChange={setType}
                  options={[...FILE_TYPE_OPTIONS]}
                />
              </>
            }
            columns={[
              {
                key: "name",
                header: "Document Name",
                cell: (r) => <span className="font-medium">{r.name}</span>,
              },
              { key: "source", header: "Source" },
              { key: "company", header: "Company", cell: () => company?.displayName },
              { key: "receivedAt", header: "Received", cell: (r) => fmtDate(r.receivedAt) },
              { key: "fileType", header: "File Type" },
              { key: "size", header: "Size", cell: (r) => fmtSize(r.size) },
              {
                key: "documentType",
                header: "Document Type",
                cell: (r) => r.documentType ?? "Unknown",
              },
              { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
              {
                key: "actions",
                header: "Actions",
                cell: (r) => (
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label="Preview"
                      onClick={() => setPreview(r)}
                    >
                      <Eye />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label="Delete document"
                      disabled={remove.isPending}
                      onClick={() => onDelete(r)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        </>
      )}

      <Dialog open={!!preview} onOpenChange={(o) => !o && setPreview(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{preview?.name}</DialogTitle>
          </DialogHeader>
          {preview && (
            <>
              <div className="flex h-56 items-center justify-center rounded border bg-muted text-sm text-muted-foreground">
                Preview is not available yet — the original file is stored locally by the backend
              </div>
              <div>
                <KV k="Document ID" v={preview.id} />
                <KV k="Source" v={preview.source} />
                <KV k="Received" v={fmtDate(preview.receivedAt)} />
                <KV k="Size" v={fmtSize(preview.size)} />
                <KV k="File type" v={preview.fileType} />
                <KV k="Document type" v={preview.documentType ?? "Unknown"} />
                <KV k="Status" v={<StatusBadge status={preview.status} />} />
              </div>
            </>
          )}
          <DialogFooter>
            <Button onClick={() => setPreview(null)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
