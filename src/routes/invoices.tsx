import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronsUpDown,
  Loader2,
  Save,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  PageHeader,
  Section,
  StatusBadge,
  Field,
  InfoNotice,
  EmptyState,
} from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useCompany, useStore } from "@/lib/store";
import { useTallyMappingSync } from "@/lib/queries/tally";
import { periodMonthStart } from "@/lib/constants";
import { useDocuments } from "@/lib/queries/documents";
import { documentsApi } from "@/lib/api/documents";
import {
  invoiceToForm,
  lineItemsSummary,
  mappingNeedsReview,
  toInvoicePayload,
  type InvoiceFormValues,
  type UiInvoice,
} from "@/lib/api/invoices";
import {
  useConfirmVendorMapping,
  useDeleteInvoice,
  useInvoiceLineItems,
  useInvoices,
  useProcessInvoice,
  useUpdateInvoice,
  useVendorMatch,
} from "@/lib/queries/invoices";
import { useVendors } from "@/lib/queries/vendors";
import { VendorMappingPanel } from "@/components/pal/vendor-mapping";
import { InvoiceVoucherPanel } from "@/components/pal/invoice-voucher";
import { InvoiceLineItemsPanel } from "@/components/pal/invoice-line-items";
import { VoucherReview } from "@/components/pal/voucher-review";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  useVouchers,
  usePostVoucherToTally,
  useCreateVoucherFromInvoice,
} from "@/lib/queries/vouchers";
import type { Voucher } from "@/lib/api/vouchers";
import { compareMoney, fmtDate, fmtMoney } from "@/lib/format";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/invoices")({
  validateSearch: (s: Record<string, unknown>): { inv?: string } =>
    typeof s.inv === "string" ? { inv: s.inv } : {},
  head: () =>
    seo(
      "Invoice Processing",
      "Review, approve, map, and export invoice data against a source document.",
    ),
  component: InvoicePage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";

/** A voucher's ledger/voucher mapping is complete only when every line has a
 * ledger, it balances, and it carries no blocking validation error. */
function ledgerMapped(voucher: Voucher | undefined): boolean {
  if (!voucher) return false;
  if (voucher.lines.some((l) => !l.ledgerId)) return false;
  if (!voucher.isBalanced) return false;
  return !voucher.validationIssues.some((i) => i.severity === "ERROR");
}

function tallyLabel(voucher: Voucher | undefined): string {
  if (!voucher) return "Not posted";
  if (voucher.status === "POSTED") return "Posted";
  if (voucher.status === "POSTING_FAILED") return "Posting failed";
  if (voucher.status === "APPROVED" || voucher.status === "AWAITING_IMPORT_CONFIRMATION")
    return "Ready";
  return "Not posted";
}

/** The single row status that drives the badge, manual-check flag and posting
 * gate. Nothing is invented: it reads the persisted invoice and voucher state. */
function rowStatus(invoice: UiInvoice, voucher: Voucher | undefined): string {
  if (voucher?.status === "POSTED") return "Posted to Tally";
  if (invoice.status === "FAILED" || voucher?.status === "POSTING_FAILED") return "Failed";
  if (invoice.status === "DRAFT" || invoice.status === "PROCESSING") return "Processing";
  const vendorOk =
    invoice.vendorMappingStatus === "MATCHED" || invoice.vendorMappingStatus === "CONFIRMED";
  if (invoice.status === "NEEDS_REVIEW" || !vendorOk || !ledgerMapped(voucher))
    return "Needs Review";
  if (voucher?.status === "APPROVED" || voucher?.status === "AWAITING_IMPORT_CONFIRMATION")
    return "Approved";
  return "Ready to Approve";
}

/** Plain-language reasons a row still needs a manual check. Reads the same
 * persisted state as rowStatus; nothing is invented. */
function reviewReasons(invoice: UiInvoice, voucher: Voucher | undefined): string[] {
  const reasons: string[] = [];
  if (invoice.status === "NEEDS_REVIEW") {
    reasons.push(invoice.validationIssues[0] ?? "Extracted fields need review");
  }
  const vendorOk =
    invoice.vendorMappingStatus === "MATCHED" || invoice.vendorMappingStatus === "CONFIRMED";
  if (!vendorOk) {
    reasons.push(
      invoice.vendorMappingStatus === "UNMAPPED"
        ? "Vendor not mapped"
        : "Vendor mapping needs confirmation",
    );
  }
  if (!voucher) {
    // A voucher needs a date; PAL never invents one. So the only reason a dated
    // invoice has no voucher is a transient auto-prepare in flight.
    reasons.push(
      invoice.invoiceDate
        ? "Accounting voucher is being prepared"
        : "Invoice date missing — add it to prepare the accounting voucher",
    );
  } else {
    if (voucher.lines.some((l) => !l.ledgerId)) reasons.push("A voucher line has no ledger");
    else if (!voucher.isBalanced) reasons.push("Voucher is not balanced");
    const error = voucher.validationIssues.find((i) => i.severity === "ERROR");
    if (error) reasons.push(error.message);
  }
  return reasons;
}

/** Posting is allowed only once a voucher is approved; a failed attempt may be
 * retried. The backend enforces the same rule, so this only gates the button. */
function canPost(voucher: Voucher | undefined): boolean {
  return voucher?.status === "APPROVED" || voucher?.status === "POSTING_FAILED";
}

/** Official Tally app icon, served from public/. */
function TallyMark({ className }: { className?: string }) {
  return <img src="/tally.png" alt="" className={cn("h-4 w-4", className)} />;
}

/** A clickable column header: first click sorts ascending, next toggles. */
function SortHeader({
  sort,
  onSort,
  col,
  label,
  align,
  thClass,
}: {
  sort: { key: string; dir: "asc" | "desc" };
  onSort: (key: string) => void;
  col: string;
  label: string;
  align?: "right";
  thClass?: string;
}) {
  const active = sort.key === col;
  const Icon = !active ? ChevronsUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th className={cn("px-3 py-2", thClass)}>
      <button
        type="button"
        onClick={() => onSort(col)}
        className={cn(
          "inline-flex items-center gap-1 font-medium hover:text-foreground",
          active && "text-foreground",
          align === "right" && "w-full justify-end",
        )}
      >
        {label}
        <Icon className={cn("h-3 w-3", !active && "opacity-40")} />
      </button>
    </th>
  );
}

/**
 * Post-to-Tally cell button. Three states, all visible rather than a single
 * dead disabled button: posted (done), ready (approved, the branded action),
 * and blocked (not yet approved — clicking opens the row so it can be approved).
 */
function PostToTallyButton({
  voucher,
  pending,
  onPost,
  onOpen,
  onRepush,
}: {
  voucher: Voucher | undefined;
  pending: boolean;
  onPost: () => void;
  onOpen: () => void;
  onRepush: () => void;
}) {
  const base =
    "inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-70";

  if (voucher?.status === "POSTED") {
    return (
      <button
        onClick={onRepush}
        disabled={pending}
        aria-label="Re-push to Tally"
        title="Posted to Tally. Click to re-push (updates the existing voucher in Tally)."
        className={cn(
          base,
          "border border-success/30 bg-success/10 text-success hover:bg-success/20",
        )}
      >
        {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
      </button>
    );
  }

  const retry = voucher?.status === "POSTING_FAILED";
  if (canPost(voucher)) {
    return (
      <button
        onClick={onPost}
        disabled={pending}
        aria-label={retry ? "Retry posting to Tally" : "Post to Tally"}
        title={retry ? "Retry posting to Tally" : "Post this approved voucher into TallyPrime"}
        className={cn(
          base,
          "bg-gradient-to-b from-primary to-primary/85 text-primary-foreground shadow-sm hover:to-primary",
        )}
      >
        {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <TallyMark />}
      </button>
    );
  }

  // Not approved yet: still interactive. Open the row so the reviewer can
  // approve, instead of presenting an inert greyed-out control.
  return (
    <button
      onClick={onOpen}
      aria-label="Approve the voucher first, then post to Tally"
      title="Approve the voucher first, then post it to Tally"
      className={cn(
        base,
        "border border-dashed border-input text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      <TallyMark className="opacity-60" />
    </button>
  );
}

function InvoicePage() {
  const navigate = useNavigate();
  const { inv } = Route.useSearch();
  const company = useCompany();
  const companyId = company?.id;
  const { financialYear, period } = useStore();
  const postingMonth = periodMonthStart(financialYear, period);

  const { data, isPending, isError, error, refetch } = useInvoices(companyId);
  const { data: documents } = useDocuments(companyId);
  const { data: vendors } = useVendors(companyId, true);
  const updateInvoice = useUpdateInvoice(companyId);
  const deleteInvoice = useDeleteInvoice(companyId);
  const confirmMapping = useConfirmVendorMapping(companyId);
  const processInvoice = useProcessInvoice(companyId);
  const { data: invoiceVouchers } = useVouchers(companyId, { sourceType: "INVOICE" });
  const postToTally = usePostVoucherToTally(companyId);
  const createVoucherFromInvoice = useCreateVoucherFromInvoice(companyId);
  const mappingSync = useTallyMappingSync();
  const learnedCompany = useRef<string | null>(null);

  // On open, silently learn ledger mappings from Tally's old vouchers once per
  // company, so invoices map to the ledger each vendor was historically booked
  // to. A Tally failure is non-fatal: the page still works without it.
  useEffect(() => {
    if (!companyId || learnedCompany.current === companyId) return;
    const tallyCompanyName = company?.legalName ?? company?.displayName ?? "";
    if (!tallyCompanyName) return;
    learnedCompany.current = companyId;
    mappingSync.mutate({ companyId, tallyCompanyName });
  }, [companyId, company?.legalName, company?.displayName, mappingSync]);
  const autoProcessed = useRef<Set<string>>(new Set());
  const autoVoucher = useRef<Set<string>>(new Set());

  const invoices = data ?? [];
  // Without ?inv the processing table is shown; a row click sets it.
  const invoice = inv ? invoices.find((i) => i.id === inv) : undefined;
  const matchPreview = useVendorMatch(
    companyId,
    invoice?.id,
    invoice?.vendorMappingStatus === "AMBIGUOUS",
  );
  const lineItems = useInvoiceLineItems(companyId, invoice?.id);
  const [form, setForm] = useState<InvoiceFormValues | null>(null);
  const [dirty, setDirty] = useState(false);
  // Voucher opened in the inline approve-and-post card from the list's Tally button.
  const [reviewVoucherId, setReviewVoucherId] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({
    key: "date",
    dir: "desc",
  });
  const [batch, setBatch] = useState("All");

  useEffect(() => {
    setForm(invoice ? invoiceToForm(invoice) : null);
    setDirty(false);
    // Re-sync when extraction writes values or the selected invoice changes.
  }, [invoice?.id, invoice?.updatedAt]); // eslint-disable-line react-hooks/exhaustive-deps

  // Invoices created from an inbox document start as an empty DRAFT. Reading
  // the source is part of selecting that invoice, so a blank draft is sent
  // through extraction once. The guard prevents an AI request on every query
  // refresh and after a failed attempt.
  useEffect(() => {
    if (!invoice || invoice.status !== "DRAFT") return;
    if (invoice.invoiceNumber || invoice.vendorName || invoice.totalAmount) return;
    if (processInvoice.isPending || autoProcessed.current.has(invoice.id)) return;
    autoProcessed.current.add(invoice.id);
    void (async () => {
      try {
        const result = await processInvoice.mutateAsync(invoice.id);
        if (!result.configured) {
          toast.info(result.message);
          return;
        }
        if (result.status === "PROCESSED") {
          toast.success(result.message);
        } else if (result.status === "NEEDS_REVIEW") {
          const issues = result.issues.length ? ` ${result.issues.length} issue(s) to review.` : "";
          toast.info(`${result.message}${issues}`);
        } else {
          toast.error(result.message);
        }
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Could not run extraction");
      }
    })();
    // The invoice fields are the trigger; the mutation object is not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    invoice?.id,
    invoice?.status,
    invoice?.invoiceNumber,
    invoice?.vendorName,
    invoice?.totalAmount,
    processInvoice.isPending,
  ]);

  // Invoice Processing owns voucher creation. Auto-prepare a draft voucher for
  // each extracted invoice that has none yet, so the Entry Details column fills
  // in (ledgers auto-mapped from Tally history) without opening every row.
  // Idempotent and guarded to run once per invoice; invoices with no date are
  // skipped because the backend will not invent a voucher date.
  useEffect(() => {
    if (!companyId) return;
    const withVoucher = new Set((invoiceVouchers ?? []).map((v) => v.sourceId));
    for (const i of invoices) {
      if (i.status !== "PROCESSED" && i.status !== "NEEDS_REVIEW") continue;
      if (!i.invoiceDate) continue;
      if (withVoucher.has(i.id) || autoVoucher.current.has(i.id)) continue;
      autoVoucher.current.add(i.id);
      createVoucherFromInvoice.mutate({ invoiceId: i.id });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId, invoices, invoiceVouchers]);

  const sourceDocument = useMemo(
    () => documents?.find((d) => d.id === invoice?.documentId) ?? null,
    [documents, invoice?.documentId],
  );

  const set = (k: keyof InvoiceFormValues, v: string) => {
    setForm((p) => (p ? { ...p, [k]: v } : p));
    setDirty(true);
  };

  if (!companyId) {
    return (
      <>
        <PageHeader title="Invoice Processing" description="Review and enter invoice data." />
        <div className="rounded-md border bg-card p-4">
          <EmptyState
            title="No company selected"
            hint="Create a company first — every invoice belongs to one."
          />
        </div>
      </>
    );
  }

  if (isPending) {
    return (
      <>
        <PageHeader title="Invoice Processing" description="Review and enter invoice data." />
        <div className="rounded-md border bg-card p-4">
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">Loading invoices…</p>
        </div>
      </>
    );
  }

  if (isError) {
    return (
      <>
        <PageHeader title="Invoice Processing" description="Review and enter invoice data." />
        <div className="rounded-md border border-destructive/40 bg-card p-6">
          <EmptyState
            title="Could not load invoices"
            hint={error instanceof Error ? error.message : "The backend is unreachable."}
          />
          <div className="flex justify-center">
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Try again
            </Button>
          </div>
        </div>
      </>
    );
  }

  if (!inv || !invoice || !form) {
    const voucherByInvoice = new Map<string, Voucher>();
    for (const v of invoiceVouchers ?? []) {
      if (v.sourceId && !voucherByInvoice.has(v.sourceId)) voucherByInvoice.set(v.sourceId, v);
    }

    // Batch code lives on the source document; map each invoice to it so the
    // table can be filtered to one upload batch.
    const batchByDoc = new Map((documents ?? []).map((d) => [d.id, d.batchCode]));
    const batchOf = (i: UiInvoice) => (i.documentId ? batchByDoc.get(i.documentId) : null) ?? null;
    // Only batches that actually back an invoice — bank-upload batches live in
    // the same documents table but must not appear here.
    const batchCodes = Array.from(
      new Set(invoices.map((i) => batchOf(i)).filter(Boolean)),
    ) as string[];

    // Failed rows are hidden from the table; the records stay in the backend.
    const rows = invoices.filter(
      (i) =>
        rowStatus(i, voucherByInvoice.get(i.id)) !== "Failed" &&
        (batch === "All" || batchOf(i) === batch),
    );

    // Comparable value per sortable column. Money is compared with compareMoney
    // (never Number()); everything else compares as its displayed string.
    const sortText = (i: UiInvoice): string => {
      const voucher = voucherByInvoice.get(i.id);
      switch (sort.key) {
        case "inv":
          return i.invoiceNumber ?? "";
        case "vendor":
          return i.vendorName ?? "";
        case "date":
          return i.invoiceDate ?? "";
        case "vmap":
          return (vendors ?? []).find((v) => v.id === i.vendorId)?.name ?? "";
        case "ledger":
          return voucher?.voucherNumber ?? "";
        case "status":
          return rowStatus(i, voucher);
        case "check":
          return rowStatus(i, voucher) === "Needs Review" ? "Yes" : "No";
        case "tally":
          return tallyLabel(voucher);
        default:
          return "";
      }
    };
    const visible = [...rows].sort((a, b) => {
      const d = sort.dir === "asc" ? 1 : -1;
      if (sort.key === "amount") return compareMoney(a.totalAmount, b.totalAmount) * d;
      return sortText(a).localeCompare(sortText(b)) * d;
    });

    const toggleSort = (key: string) =>
      setSort((s) => ({ key, dir: s.key === key && s.dir === "asc" ? "desc" : "asc" }));

    const postRow = async (voucher: Voucher) => {
      try {
        const result = await postToTally.mutateAsync({ id: voucher.id, postingMonth });
        if (result.success) {
          toast.success(result.already_posted ? "Already posted to Tally" : "Posted to Tally");
        } else {
          toast.error(result.message || "Tally posting failed");
        }
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Could not post to Tally");
      }
    };

    const repushRow = async (voucher: Voucher) => {
      if (
        !window.confirm(
          `Re-push voucher ${voucher.voucherNumber} to Tally? Tally updates the existing voucher (matched by REMOTEID); it does not create a duplicate.`,
        )
      )
        return;
      try {
        const result = await postToTally.mutateAsync({
          id: voucher.id,
          postingMonth,
          force: true,
        });
        if (result.success) {
          toast.success("Re-pushed to Tally");
        } else {
          toast.error(result.message || "Tally re-push failed");
        }
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Could not re-push to Tally");
      }
    };

    return (
      <>
        <PageHeader
          title="Invoice Processing"
          description="Batch-processed invoices. Open a row to review; post approved ones to Tally."
          actions={
            <select
              aria-label="Filter by batch code"
              className={selectCls}
              value={batch}
              onChange={(e) => setBatch(e.target.value)}
            >
              <option value="All">All batches</option>
              {batchCodes.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          }
        />
        <InfoNotice>
          Upload documents in the Document Inbox to process a batch. Each row shows the extracted
          invoice and its mapping exactly as read — nothing is inferred. Rows needing a check must
          be opened and corrected before they can be approved and posted.
        </InfoNotice>

        {!visible.length ? (
          <div className="rounded-md border bg-card p-4">
            <EmptyState
              title="No invoices for this company"
              hint="Upload documents in the Document Inbox to process a batch."
            />
          </div>
        ) : (
          <div className="overflow-x-auto rounded-md border bg-card">
            <table className="w-full min-w-[960px] text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <SortHeader sort={sort} onSort={toggleSort} col="inv" label="Invoice No." />
                  <SortHeader sort={sort} onSort={toggleSort} col="vendor" label="Vendor" />
                  <SortHeader sort={sort} onSort={toggleSort} col="date" label="Date" />
                  <SortHeader
                    sort={sort}
                    onSort={toggleSort}
                    col="amount"
                    label="Amount"
                    align="right"
                  />
                  <SortHeader
                    sort={sort}
                    onSort={toggleSort}
                    col="ledger"
                    label="Entry Details"
                    thClass="min-w-[300px]"
                  />
                  <SortHeader sort={sort} onSort={toggleSort} col="status" label="Status" />
                  <SortHeader sort={sort} onSort={toggleSort} col="tally" label="Tally" />
                  <th className="px-3 py-2 text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((i) => {
                  const voucher = voucherByInvoice.get(i.id);
                  const status = rowStatus(i, voucher);
                  const entryLines = [...(voucher?.lines ?? [])].sort(
                    (a, b) => a.lineNumber - b.lineNumber,
                  );
                  const open = () => navigate({ to: "/invoices", search: { inv: i.id } });
                  return (
                    <tr
                      key={i.id}
                      className="cursor-pointer border-b last:border-0 hover:bg-muted/40"
                      onClick={open}
                    >
                      <td className="px-3 py-2 font-medium">{i.invoiceNumber || "Unnumbered"}</td>
                      <td className="px-3 py-2 text-muted-foreground">{i.vendorName || "—"}</td>
                      <td className="px-3 py-2 num">
                        {i.invoiceDate ? fmtDate(i.invoiceDate) : "—"}
                      </td>
                      <td className="px-3 py-2 num text-right font-bold">
                        {fmtMoney(i.totalAmount)}
                      </td>
                      <td className="px-3 py-2">
                        {voucher ? (
                          <div className="flex flex-col leading-tight">
                            <span className="num font-semibold">{voucher.voucherNumber}</span>
                            {entryLines.map((l) => (
                              <span key={l.id} className="text-xs text-muted-foreground">
                                {l.ledgerName || "—"} —{" "}
                                <span
                                  className={cn(
                                    "num font-medium",
                                    l.side === "Dr" ? "text-success" : "text-destructive",
                                  )}
                                >
                                  ₹{fmtMoney(l.amount)} {l.side}
                                </span>
                              </span>
                            ))}
                          </div>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {status === "Needs Review" ? (
                          <div className="flex items-start gap-1.5 text-warning">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                            <ul className="list-disc space-y-0.5 pl-4 text-xs">
                              {(reviewReasons(i, voucher).length
                                ? reviewReasons(i, voucher)
                                : ["Manual check required"]
                              ).map((r) => (
                                <li key={r}>{r}</li>
                              ))}
                            </ul>
                          </div>
                        ) : (
                          <StatusBadge status={status} />
                        )}
                        {voucher?.status === "POSTED" && (
                          <div className="mt-1 text-xs text-muted-foreground">
                            {voucher.postingReference && (
                              <div className="num">Ref {voucher.postingReference}</div>
                            )}
                            {voucher.postedAt && (
                              <div>
                                {fmtDate(voucher.postedAt)}{" "}
                                {new Date(voucher.postedAt).toLocaleTimeString([], {
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })}
                              </div>
                            )}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <StatusBadge status={tallyLabel(voucher)} />
                      </td>
                      <td className="px-3 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end">
                          <PostToTallyButton
                            voucher={voucher}
                            pending={postToTally.isPending}
                            onPost={() => voucher && postRow(voucher)}
                            onRepush={() => voucher && repushRow(voucher)}
                            onOpen={() => (voucher ? setReviewVoucherId(voucher.id) : open())}
                          />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <Dialog
          open={Boolean(reviewVoucherId)}
          onOpenChange={(o) => !o && setReviewVoucherId(null)}
        >
          <DialogContent className="max-h-[90vh] max-w-5xl overflow-y-auto">
            <DialogHeader>
              <DialogTitle>Approve &amp; post voucher</DialogTitle>
              <DialogDescription>
                Review the voucher, submit and approve it, then post it to Tally — all here.
              </DialogDescription>
            </DialogHeader>
            {reviewVoucherId && (
              <VoucherReview
                companyId={companyId}
                voucherId={reviewVoucherId}
                onDeleted={() => setReviewVoucherId(null)}
                approveAndPost
              />
            )}
          </DialogContent>
        </Dialog>
      </>
    );
  }

  const remove = async () => {
    try {
      await deleteInvoice.mutateAsync(invoice.id);
      toast.success("Invoice rejected. The source document is still in the inbox.");
      navigate({ to: "/invoices", search: {} });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not reject invoice");
    }
  };

  const mapVendor = async (vendorId: string | null) => {
    try {
      await confirmMapping.mutateAsync({ id: invoice.id, vendorId });
      toast.success(vendorId ? "Vendor mapping confirmed" : "Vendor suggestion rejected");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not update vendor mapping");
    }
  };

  const saveForm = async () => {
    await updateInvoice.mutateAsync({ id: invoice.id, body: toInvoicePayload(form) });
    setDirty(false);
  };

  const saveDraft = async () => {
    try {
      await saveForm();
      toast.success("Draft saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save draft");
    }
  };

  const approveData = async () => {
    if (invoice.status === "PROCESSED") {
      toast.warning("This invoice is already approved.");
      return;
    }
    if (mappingNeedsReview(invoice.vendorMappingStatus)) {
      toast.warning("Confirm or reject the vendor suggestion before approving the invoice data.");
      return;
    }
    try {
      await saveForm();
      if (invoice.status === "DRAFT" || invoice.status === "FAILED") {
        // The backend lifecycle requires processing before an invoice can be
        // approved. A manual approval moves through that state explicitly.
        await updateInvoice.mutateAsync({ id: invoice.id, body: { status: "PROCESSING" } });
      }
      await updateInvoice.mutateAsync({ id: invoice.id, body: { status: "PROCESSED" } });
      toast.success("Invoice data approved");
      navigate({ to: "/invoices", search: {} });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not approve invoice data");
    }
  };

  const rejectInvoice = async () => {
    if (
      !window.confirm("Reject and delete this invoice? The source document stays in the inbox.")
    ) {
      return;
    }
    await remove();
  };

  return (
    <>
      <PageHeader
        title="Invoice Processing"
        description="Review, approve, map, and export invoice data against a source document."
        actions={
          <select
            aria-label="Select invoice"
            className={selectCls}
            value={invoice.id}
            onChange={(e) => navigate({ to: "/invoices", search: { inv: e.target.value } })}
          >
            {invoices.map((i) => (
              <option key={i.id} value={i.id}>
                {i.invoiceNumber || "Unnumbered invoice"} · {i.status}
              </option>
            ))}
          </select>
        }
      />
      <InfoNotice>
        Blank drafts are read from the source document automatically. Values are stored as read or
        typed; missing fields remain blank and nothing is recalculated or corrected.
      </InfoNotice>

      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        Review status: <StatusBadge status={invoice.status.replace("_", " ")} /> Source:{" "}
        <StatusBadge status={invoice.source === "MANUAL" ? "Manual entry" : invoice.source} />
      </div>

      {invoice.validationIssues.length > 0 && (
        <div className="mb-3 rounded-md border border-warning/40 bg-warning/5 p-3">
          <p className="text-xs font-semibold text-foreground">
            Extraction needs review — figures are shown exactly as read, not corrected
          </p>
          <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
            {invoice.validationIssues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(320px,2fr)_3fr]">
        <Section title="Source Document" className="self-start xl:sticky xl:top-4">
          {sourceDocument ? (
            <>
              <div className="relative overflow-hidden rounded border bg-muted">
                {sourceDocument.fileType === "PDF" ? (
                  <iframe
                    title={sourceDocument.name}
                    src={documentsApi.fileUrl(companyId, sourceDocument.id)}
                    className="h-[520px] w-full"
                  />
                ) : sourceDocument.fileType === "JPEG" || sourceDocument.fileType === "PNG" ? (
                  <img
                    src={documentsApi.fileUrl(companyId, sourceDocument.id)}
                    alt={sourceDocument.name}
                    className="max-h-[520px] w-full object-contain"
                  />
                ) : (
                  <div className="flex h-[240px] flex-col items-center justify-center gap-2 p-4 text-center text-sm text-muted-foreground">
                    <p>Preview is not available for {sourceDocument.fileType} files.</p>
                    <a
                      href={documentsApi.fileUrl(companyId, sourceDocument.id)}
                      target="_blank"
                      rel="noreferrer"
                      className="text-primary underline"
                    >
                      Open document
                    </a>
                  </div>
                )}
                {processInvoice.isPending && (
                  <div className="absolute inset-0 flex items-center justify-center bg-background/80 p-4 text-center text-sm font-medium">
                    Reading the document with AI…
                  </div>
                )}
              </div>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span className="truncate font-medium text-foreground">{sourceDocument.name}</span>
                <div className="flex gap-3">
                  <a
                    href={documentsApi.fileUrl(companyId, sourceDocument.id)}
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary underline"
                  >
                    Open
                  </a>
                  <a
                    href={documentsApi.fileUrl(companyId, sourceDocument.id, true)}
                    className="text-primary underline"
                  >
                    Download
                  </a>
                </div>
              </div>
            </>
          ) : (
            <EmptyState
              title="Source document not found"
              hint="The invoice is linked to a document that is no longer in the inbox."
            />
          )}
          <div className="mt-3 text-xs text-muted-foreground">
            Document ID <span className="num">{invoice.documentId}</span>
          </div>
        </Section>

        <div className="flex flex-col gap-4">
          <Section title="Invoice Fields">
            <fieldset disabled={processInvoice.isPending} className="contents">
              <div className="grid gap-3 md:grid-cols-3">
                <Field label="Invoice direction">
                  <select
                    className={selectCls}
                    value={form.direction}
                    onChange={(e) => set("direction", e.target.value)}
                  >
                    <option value="PURCHASE">Purchase (expense / creditor)</option>
                    <option value="SALES">Sales (customer / revenue)</option>
                  </select>
                </Field>
                <Field label="Vendor Name (as printed)">
                  <Input
                    value={form.vendorName}
                    onChange={(e) => set("vendorName", e.target.value)}
                  />
                </Field>
                <Field label="Invoice Number">
                  <Input
                    value={form.invoiceNumber}
                    onChange={(e) => set("invoiceNumber", e.target.value)}
                  />
                </Field>
                <Field label="Invoice Date">
                  <Input
                    type="date"
                    value={form.invoiceDate}
                    onChange={(e) => set("invoiceDate", e.target.value)}
                  />
                </Field>
                <Field label="Due Date">
                  <Input
                    type="date"
                    value={form.dueDate}
                    onChange={(e) => set("dueDate", e.target.value)}
                  />
                </Field>
                <Field label="Vendor GSTIN">
                  <Input
                    className="num"
                    value={form.vendorGstin}
                    onChange={(e) => set("vendorGstin", e.target.value.toUpperCase())}
                  />
                </Field>
                <Field label="Vendor PAN">
                  <Input
                    className="num"
                    value={form.vendorPan}
                    onChange={(e) => set("vendorPan", e.target.value.toUpperCase())}
                  />
                </Field>
                <Field label="Buyer GSTIN">
                  <Input
                    className="num"
                    value={form.buyerGstin}
                    onChange={(e) => set("buyerGstin", e.target.value.toUpperCase())}
                  />
                </Field>
                <Field label="Purchase Order No.">
                  <Input value={form.poNumber} onChange={(e) => set("poNumber", e.target.value)} />
                </Field>
                <Field label="Currency">
                  <Input
                    maxLength={3}
                    value={form.currency}
                    onChange={(e) => set("currency", e.target.value.toUpperCase())}
                  />
                </Field>
                {(
                  [
                    ["Taxable Amount", "taxableAmount"],
                    ["CGST", "cgstAmount"],
                    ["SGST", "sgstAmount"],
                    ["IGST", "igstAmount"],
                    ["Cess", "cessAmount"],
                    ["Round-off", "roundOff"],
                    ["Total Invoice Value", "totalAmount"],
                  ] as [string, keyof InvoiceFormValues][]
                ).map(([label, key]) => (
                  <Field key={key} label={label}>
                    <Input
                      className="num"
                      inputMode="decimal"
                      placeholder="0.00"
                      value={form[key]}
                      onChange={(e) => set(key, e.target.value)}
                    />
                  </Field>
                ))}
              </div>
            </fieldset>
            <p className="mt-3 text-xs text-muted-foreground">
              Amounts are entered as decimal text and stored exactly as typed. Nothing is rounded or
              recalculated for you.
            </p>
          </Section>

          <InvoiceLineItemsPanel
            key={`lines-${invoice.id}`}
            companyId={companyId}
            invoiceId={invoice.id}
          />

          <Section title="Vendor Mapping">
            <VendorMappingPanel
              key={invoice.id}
              status={invoice.vendorMappingStatus}
              matchMethod={invoice.vendorMatchMethod}
              vendorId={invoice.vendorId}
              suggestedVendorId={invoice.suggestedVendorId}
              previousVendorId={invoice.previousVendorId}
              vendors={vendors ?? []}
              candidates={matchPreview.data?.candidates ?? []}
              onConfirm={mapVendor}
              onReject={() => mapVendor(null)}
              onMap={mapVendor}
              pending={confirmMapping.isPending}
              error={confirmMapping.error instanceof Error ? confirmMapping.error.message : null}
              particulars={lineItemsSummary(lineItems.data ?? [])}
            />
          </Section>

          <InvoiceVoucherPanel
            key={`${companyId}-${invoice.id}`}
            companyId={companyId}
            invoiceId={invoice.id}
          />

          <div className="sticky bottom-0 flex flex-col gap-2 rounded-md border bg-card p-3">
            {/* Approve Data leads; Reject sits apart so it is never a misclick. */}
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                className="flex-1"
                onClick={approveData}
                disabled={updateInvoice.isPending || processInvoice.isPending}
              >
                <Check />
                Approve Data
              </Button>
              <Button
                size="sm"
                variant="destructive"
                className="flex-1"
                onClick={rejectInvoice}
                disabled={deleteInvoice.isPending || processInvoice.isPending}
              >
                <X />
                Reject
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="flex-1"
                onClick={saveDraft}
                disabled={!dirty || updateInvoice.isPending || processInvoice.isPending}
              >
                <Save />
                Save Draft
              </Button>
            </div>
            {mappingNeedsReview(invoice.vendorMappingStatus) && (
              <p className="text-xs text-muted-foreground">
                Confirm or reject the vendor suggestion before approving the invoice data.
              </p>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
