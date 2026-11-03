import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { Eye, Loader2, Trash2 } from "lucide-react";
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
import { FILE_TYPE_OPTIONS, nextBatchCode } from "@/lib/api/documents";
import { useDeleteDocument, useDocuments, useUploadDocuments } from "@/lib/queries/documents";
import { useInvoices, useProcessDocumentsBatch, type BatchProgress } from "@/lib/queries/invoices";
import type { DocStatus, InboxDocument } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/inbox")({
  head: () =>
    seo("Document Inbox", "Receive invoices and financial documents via email or manual upload."),
  component: InboxPage,
});

const STATUSES: DocStatus[] = ["Uploaded", "Processing", "Extracted", "Needs Review", "Failed"];
const ACCEPT = ".pdf,.jpg,.jpeg,.png,.xls,.xlsx,.docx";
const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";
/** One processing batch is capped so extraction stays responsive and reviewable. */
const MAX_BATCH = 10;

function InboxPage() {
  const s = useStore();
  const company = useCompany();
  const companyId = company?.id;
  const { data, isPending, isError, error, refetch } = useDocuments(companyId);
  const { data: invoices } = useInvoices(companyId);
  const upload = useUploadDocuments(companyId);
  const processBatch = useProcessDocumentsBatch(companyId);
  const remove = useDeleteDocument(companyId);
  const [tab, setTab] = useState("all");
  const [status, setStatus] = useState("All");
  const [type, setType] = useState("All");
  const [batch, setBatch] = useState("All");
  const [lastBatch, setLastBatch] = useState<string | null>(null);
  const [preview, setPreview] = useState<InboxDocument | null>(null);
  const [staged, setStaged] = useState<File[]>([]);
  const [progress, setProgress] = useState<BatchProgress | null>(null);
  // Per-document cards for the live extraction panel. Session-only; the
  // persisted status lives on each document and is shown in the table below.
  const [batchItems, setBatchItems] = useState<
    {
      id: string;
      name: string;
      status: "queued" | "extracting" | "completed" | "review" | "failed";
    }[]
  >([]);

  const docs = data ?? [];

  // Extraction only sets a document to "Extracted"; whether it is fully done or
  // still needs a human is the invoice's (and its voucher's) verdict, so the row
  // status is read from the linked invoice. This survives a refresh because the
  // invoice status is persisted in the backend.
  const invStatusByDoc = useMemo(() => {
    const m = new Map<string, string>();
    for (const i of invoices ?? []) m.set(i.documentId, i.status);
    return m;
  }, [invoices]);
  const displayStatus = (d: InboxDocument): DocStatus => {
    if (d.status === "Extracted") {
      const inv = invStatusByDoc.get(d.id);
      if (inv === "NEEDS_REVIEW") return "Needs Review";
      if (inv === "FAILED") return "Failed";
    }
    return d.status;
  };

  const busy = upload.isPending || processBatch.isPending;

  const submit = async () => {
    if (!staged.length) return;
    if (staged.length > MAX_BATCH) {
      toast.error(`Maximum ${MAX_BATCH} documents can be processed at a time.`);
      return;
    }
    const files = staged;
    // Clear immediately so the button cannot be double-submitted while the
    // upload is in flight.
    setStaged([]);
    // One simple sequential code for this whole upload, so the inbox can be
    // filtered to it later.
    const batchCode = nextBatchCode(docs.map((d) => d.batchCode));
    let saved: InboxDocument[];
    try {
      saved = await upload.mutateAsync({ files, batchCode });
      setLastBatch(batchCode);
      toast.success(
        `${saved.length} document${saved.length === 1 ? "" : "s"} uploaded — batch ${batchCode}`,
      );
    } catch (e) {
      // Put the files back so the user can retry or adjust them.
      setStaged((prev) => [...files, ...prev]);
      toast.error(e instanceof Error ? e.message : "Upload failed");
      return;
    }
    // Extraction only: read each document's invoice fields one by one. No
    // vendor/ledger mapping, voucher or Tally here — those are Invoice
    // Processing. The upload already succeeded, so a read failure only leaves
    // that one document for a retry.
    setBatchItems(saved.map((d) => ({ id: d.id, name: d.name, status: "queued" as const })));
    setProgress({ done: 0, total: saved.length, last: null });
    try {
      const results = await processBatch.mutateAsync({
        documentIds: saved.map((d) => d.id),
        onProgress: (p) => {
          setProgress(p);
          // Mark the just-finished document, and light up the next one.
          setBatchItems((prev) =>
            prev.map((it, idx) => {
              if (p.last && it.id === p.last.documentId) {
                return {
                  ...it,
                  status:
                    p.last.status === "FAILED"
                      ? "failed"
                      : p.last.status === "NEEDS_REVIEW"
                        ? "review"
                        : "completed",
                };
              }
              if (idx === p.done && p.done < p.total) return { ...it, status: "extracting" };
              return it;
            }),
          );
        },
      });
      const review = results.filter(
        (r) => r.status === "NEEDS_REVIEW" || r.status === "FAILED",
      ).length;
      if (results.every((r) => r.status === "NOT_CONFIGURED")) {
        toast.info("Extraction is not configured; documents are stored for manual entry.");
      } else if (review) {
        toast.warning(
          `${review} of ${results.length} document(s) need review in Invoice Processing.`,
        );
      } else {
        toast.success("All documents extracted. They are ready in Invoice Processing.");
      }
    } catch (e) {
      setProgress(null);
      toast.error(e instanceof Error ? e.message : "Extraction failed");
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

  const batchCodes = useMemo(
    () => Array.from(new Set(docs.map((d) => d.batchCode).filter(Boolean))) as string[],
    [docs],
  );

  const rows = docs.filter((d) => {
    const ds = displayStatus(d);
    if (tab === "email" && d.source !== "Email") return false;
    if (tab === "review" && ds !== "Needs Review") return false;
    if (tab === "failed" && ds !== "Failed") return false;
    return (
      (status === "All" || ds === status) &&
      (type === "All" || d.fileType === type) &&
      (batch === "All" || d.batchCode === batch)
    );
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
          <Button
            disabled={!staged.length || staged.length > MAX_BATCH || busy || !companyId}
            onClick={submit}
          >
            {upload.isPending
              ? "Uploading…"
              : processBatch.isPending
                ? "Processing…"
                : "Upload Files"}
          </Button>
          {staged.length > MAX_BATCH && (
            <p className="text-xs text-destructive">
              Maximum {MAX_BATCH} documents can be processed at a time.
            </p>
          )}
        </div>
      </div>

      {batchItems.length > 0 && (
        <div className="mb-4 rounded-md border bg-card p-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold">
              Extracting invoices{lastBatch ? ` · batch ${lastBatch}` : ""}
            </h3>
            {progress && (
              <span className="num text-xs font-medium text-muted-foreground">
                {progress.done}/{progress.total} completed ·{" "}
                {Math.round((progress.done / progress.total) * 100)}%
              </span>
            )}
          </div>
          {progress && (
            <div className="mb-3 h-2 w-full overflow-hidden rounded bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${(progress.done / progress.total) * 100}%` }}
              />
            </div>
          )}
          <ul className="divide-y rounded border text-sm">
            {batchItems.map((it) => (
              <li key={it.id} className="flex items-center justify-between gap-2 px-3 py-2">
                <span className="truncate">{it.name}</span>
                <span className="flex shrink-0 items-center gap-1.5 text-xs">
                  {it.status === "extracting" && (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                      <span className="text-primary">Extracting…</span>
                    </>
                  )}
                  {it.status === "completed" && (
                    <span className="font-medium text-success">✓ Completed</span>
                  )}
                  {it.status === "review" && (
                    <span className="font-medium text-warning">Needs review</span>
                  )}
                  {it.status === "failed" && (
                    <span className="font-medium text-destructive">Failed</span>
                  )}
                  {it.status === "queued" && <span className="text-muted-foreground">Queued</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

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
            wrap
            rows={rows}
            rowKey={(r) => r.id}
            searchKeys={(r) => `${r.name} ${r.reviewer} ${r.id} ${r.batchCode ?? ""}`}
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
                {batchCodes.length > 0 && (
                  <FilterSelect
                    label="Batch"
                    value={batch}
                    onChange={setBatch}
                    options={batchCodes}
                  />
                )}
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
              {
                key: "batchCode",
                header: "Batch",
                cell: (r) =>
                  r.batchCode ? (
                    <span className="num text-xs">{r.batchCode}</span>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  ),
              },
              { key: "size", header: "Size", cell: (r) => fmtSize(r.size) },
              {
                key: "status",
                header: "Status",
                cell: (r) => <StatusBadge status={displayStatus(r)} />,
              },
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
                <KV k="Batch" v={preview.batchCode ?? "—"} />
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
