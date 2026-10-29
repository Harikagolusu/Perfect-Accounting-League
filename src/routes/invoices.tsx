import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Save, X } from "lucide-react";
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
import { useCompany } from "@/lib/store";
import { useDocuments } from "@/lib/queries/documents";
import { documentsApi } from "@/lib/api/documents";
import {
  invoiceToForm,
  lineItemsSummary,
  mappingNeedsReview,
  toInvoicePayload,
  type InvoiceFormValues,
} from "@/lib/api/invoices";
import {
  useConfirmVendorMapping,
  useCreateInvoice,
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

function InvoicePage() {
  const navigate = useNavigate();
  const { inv } = Route.useSearch();
  const company = useCompany();
  const companyId = company?.id;

  const { data, isPending, isError, error, refetch } = useInvoices(companyId);
  const { data: documents } = useDocuments(companyId);
  const { data: vendors } = useVendors(companyId, true);
  const createInvoice = useCreateInvoice(companyId);
  const updateInvoice = useUpdateInvoice(companyId);
  const deleteInvoice = useDeleteInvoice(companyId);
  const confirmMapping = useConfirmVendorMapping(companyId);
  const processInvoice = useProcessInvoice(companyId);
  const autoProcessed = useRef<Set<string>>(new Set());

  const invoices = data ?? [];
  const invoice = invoices.find((i) => i.id === inv) ?? invoices[0];
  const matchPreview = useVendorMatch(
    companyId,
    invoice?.id,
    invoice?.vendorMappingStatus === "AMBIGUOUS",
  );
  const lineItems = useInvoiceLineItems(companyId, invoice?.id);
  const [form, setForm] = useState<InvoiceFormValues | null>(null);
  const [dirty, setDirty] = useState(false);

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

  if (!invoices.length || !inv || !invoice || !form) {
    const unlinked = (documents ?? []).filter((d) => !invoices.some((i) => i.documentId === d.id));
    return (
      <>
        <PageHeader title="Invoice Processing" description="Review and enter invoice data." />
        <InfoNotice>
          Create a draft from a document and its blank fields are read automatically. Values are
          stored as read or typed; missing fields stay blank and nothing is inferred or corrected.
        </InfoNotice>
        <div className="mb-4 rounded-md border bg-card p-4">
          <div className="flex flex-col items-center justify-center gap-2 py-4 text-center">
            <p className="text-sm font-medium text-foreground">
              {!invoices.length
                ? "No invoices for this company"
                : invoices.every((i) => i.status === "PROCESSED")
                  ? "All invoices processed"
                  : "Select an invoice"}
            </p>
            <p className="text-xs text-muted-foreground">
              {unlinked.length
                ? "Choose a document below to start entering invoice data."
                : "Upload a document in the Document Inbox first."}
            </p>
          </div>
          {unlinked.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
              <span className="text-sm text-muted-foreground">Create an invoice from</span>
              <select
                aria-label="Select document"
                className={selectCls}
                value=""
                onChange={async (e) => {
                  if (!e.target.value) return;
                  try {
                    const created = await createInvoice.mutateAsync({
                      document_id: e.target.value,
                    });
                    navigate({ to: "/invoices", search: { inv: created.id } });
                    toast.success("Invoice draft created");
                  } catch (err) {
                    toast.error(err instanceof Error ? err.message : "Could not create invoice");
                  }
                }}
              >
                <option value="">Choose a document…</option>
                {unlinked.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
        {invoices.length > 0 && (
          <div className="mb-4 rounded-md border bg-card">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="px-4 py-2">Invoice</th>
                  <th className="px-4 py-2">Vendor</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((i) => (
                  <tr key={i.id} className="border-b last:border-0 hover:bg-muted/40">
                    <td className="px-4 py-2 font-medium">{i.invoiceNumber || "Unnumbered"}</td>
                    <td className="px-4 py-2 text-muted-foreground">{i.vendorName || "—"}</td>
                    <td className="px-4 py-2">
                      <StatusBadge status={i.status.replace("_", " ")} />
                    </td>
                    <td className="px-4 py-2 text-right">
                      {i.status !== "PROCESSED" && (
                        <button
                          className="text-xs text-primary underline"
                          onClick={() => navigate({ to: "/invoices", search: { inv: i.id } })}
                        >
                          Open
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
