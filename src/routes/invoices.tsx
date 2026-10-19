import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Check, Save, Send, Trash2, Plus, Sparkles } from "lucide-react";
import {
  PageHeader,
  Section,
  StatusBadge,
  Field,
  DemoNotice,
  EmptyState,
} from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useCompany, useStore } from "@/lib/store";
import { useDocuments } from "@/lib/queries/documents";
import {
  STATUS_OPTIONS,
  invoiceToForm,
  toInvoicePayload,
  type InvoiceFormValues,
} from "@/lib/api/invoices";
import {
  useCreateInvoice,
  useDeleteInvoice,
  useInvoices,
  useProcessInvoice,
  useUpdateInvoice,
} from "@/lib/queries/invoices";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/invoices")({
  validateSearch: (s: Record<string, unknown>): { inv?: string } =>
    typeof s.inv === "string" ? { inv: s.inv } : {},
  head: () => seo("Invoice Processing", "Review and enter invoice data against a source document."),
  component: InvoicePage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";
const dash = (v: string | null | undefined) => (v ? v : "—");

function InvoicePage() {
  const s = useStore();
  const navigate = useNavigate();
  const { inv } = Route.useSearch();
  const company = useCompany();
  const companyId = company?.id;

  const { data, isPending, isError, error, refetch } = useInvoices(companyId);
  const { data: documents } = useDocuments(companyId);
  const createInvoice = useCreateInvoice(companyId);
  const updateInvoice = useUpdateInvoice(companyId);
  const deleteInvoice = useDeleteInvoice(companyId);
  const process = useProcessInvoice(companyId);

  const invoices = data ?? [];
  const invoice = invoices.find((i) => i.id === inv) ?? invoices[0];
  const [form, setForm] = useState<InvoiceFormValues | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setForm(invoice ? invoiceToForm(invoice) : null);
    setDirty(false);
  }, [invoice?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const documentName = useMemo(() => {
    const match = documents?.find((d) => d.id === invoice?.documentId);
    return match?.name ?? null;
  }, [documents, invoice?.documentId]);

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

  if (!invoices.length || !invoice || !form) {
    const unlinked = (documents ?? []).filter((d) => !invoices.some((i) => i.documentId === d.id));
    return (
      <>
        <PageHeader title="Invoice Processing" description="Review and enter invoice data." />
        <DemoNotice>
          Automatic extraction is not configured, so invoices are created from a document and the
          fields are entered by hand. Nothing on this page is machine-read.
        </DemoNotice>
        <div className="rounded-md border bg-card p-4">
          <EmptyState
            title="No invoices for this company"
            hint={
              unlinked.length
                ? "Choose a document below to start entering invoice data."
                : "Upload a document in the Document Inbox first."
            }
          />
          {unlinked.length > 0 && (
            <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
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
      </>
    );
  }

  const save = async () => {
    try {
      await updateInvoice.mutateAsync({ id: invoice.id, body: toInvoicePayload(form) });
      setDirty(false);
      toast.success("Invoice saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save invoice");
    }
  };

  const move = async (status: (typeof STATUS_OPTIONS)[number]) => {
    try {
      await updateInvoice.mutateAsync({ id: invoice.id, body: { status } });
      toast.success(`Invoice moved to ${status.replace("_", " ")}`);
      s.log({
        module: "Invoice Processing",
        action: "Status changed",
        recordId: invoice.invoiceNumber ?? invoice.id,
        previous: invoice.status,
        next: status,
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Not a valid status change");
    }
  };

  const runExtraction = async () => {
    try {
      const result = await process.mutateAsync(invoice.id);
      // The backend is honest about having no engine, and the UI says so.
      toast[result.configured ? "success" : "info"](result.message);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not run extraction");
    }
  };

  const remove = async () => {
    try {
      await deleteInvoice.mutateAsync(invoice.id);
      toast.success("Invoice deleted. The source document is still in the inbox.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete invoice");
    }
  };

  return (
    <>
      <PageHeader
        title="Invoice Processing"
        description="Review and enter invoice data against a source document."
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
      <DemoNotice>
        Automatic extraction is not configured. Every value below was entered by hand and is stored
        exactly as typed — nothing is inferred, corrected or read from the file.
      </DemoNotice>

      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        Review status: <StatusBadge status={invoice.status.replace("_", " ")} /> Source:{" "}
        <StatusBadge status={invoice.source === "MANUAL" ? "Manual entry" : invoice.source} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(320px,2fr)_3fr]">
        <Section title="Source Document">
          <div className="flex h-[320px] items-center justify-center rounded border bg-muted p-4 text-center text-sm text-muted-foreground">
            <div>
              <p className="font-medium text-foreground">{dash(documentName)}</p>
              <p className="mt-1">
                The original file stays in the Document Inbox. Preview and download are not
                available yet.
              </p>
            </div>
          </div>
          <div className="mt-3 text-xs text-muted-foreground">
            Document ID <span className="num">{invoice.documentId}</span>
          </div>
        </Section>

        <div className="flex flex-col gap-4">
          <Section title="Invoice Fields">
            <div className="grid gap-3 md:grid-cols-3">
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
            <p className="mt-3 text-xs text-muted-foreground">
              Amounts are entered as decimal text and stored exactly as typed. Nothing is rounded or
              recalculated for you.
            </p>
          </Section>

          <Section title="Line Items">
            <EmptyState
              title="Line items are not captured yet"
              hint="They arrive with a later step; this invoice currently holds header amounts only."
            />
          </Section>

          <Section title="Ledger Mapping">
            <EmptyState
              title="Vendor mapping is not connected yet"
              hint="Mapping a vendor to a ledger is its own later step."
            />
          </Section>

          <div className="sticky bottom-0 flex flex-wrap gap-2 rounded-md border bg-card p-3">
            <Button variant="outline" onClick={save} disabled={!dirty || updateInvoice.isPending}>
              <Save />
              Save
            </Button>
            <Button variant="outline" onClick={runExtraction} disabled={process.isPending}>
              <Sparkles />
              Try automatic extraction
            </Button>
            <Button variant="outline" onClick={() => move("NEEDS_REVIEW")}>
              <Send />
              Send to review
            </Button>
            <Button onClick={() => move("PROCESSED")}>
              <Check />
              Mark processed
            </Button>
            <Button variant="outline" onClick={() => navigate({ to: "/invoices" })}>
              <Plus />
              New invoice from document
            </Button>
            <Button variant="destructive" onClick={remove} disabled={deleteInvoice.isPending}>
              <Trash2 />
              Delete invoice
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
