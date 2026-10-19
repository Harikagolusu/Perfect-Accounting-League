import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { ZoomIn, ZoomOut, ChevronLeft, ChevronRight, Download, Save, Check, Send, X, AlertTriangle, Plus, Trash2 } from "lucide-react";
import { PageHeader, Section, StatusBadge, ConfidenceBadge, Field, DemoNotice, EmptyState } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { useScoped, useStore } from "@/lib/store";
import { buildInvoice, EXPENSE_LEDGERS, PARENT_GROUPS } from "@/lib/mock-data";
import { fmtINR, GSTIN_RE, PAN_RE } from "@/lib/format";
import { exportFile } from "@/lib/export";
import type { Invoice } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/invoices")({
  validateSearch: (s: Record<string, unknown>): { doc?: string } => (typeof s.doc === "string" ? { doc: s.doc } : {}),
  head: () => seo("Invoice Processing", "Review extracted invoice data, line items and ledger mappings."),
  component: InvoicePage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";

export function invoiceTotals(inv: Invoice) {
  const taxable = inv.lineItems.reduce((s, l) => s + l.qty * l.unitPrice, 0);
  const lineTax = inv.lineItems.reduce((s, l) => s + (l.qty * l.unitPrice * l.taxRate) / 100, 0);
  const headerTax = inv.cgst + inv.sgst + inv.igst + inv.cess;
  return { taxable, lineTax, headerTax, total: taxable + headerTax + inv.roundOff };
}

function InvoicePage() {
  const s = useStore();
  const navigate = useNavigate();
  const { doc } = Route.useSearch();
  const candidates = useScoped(s.documents).filter((d) => ["Extracted", "Needs Review", "Approved"].includes(d.status));
  const docId = doc && candidates.some((c) => c.id === doc) ? doc : candidates[0]?.id;
  const source = candidates.find((c) => c.id === docId);
  const [inv, setInv] = useState<Invoice | null>(null);
  const [zoom, setZoom] = useState(100);
  const [page, setPage] = useState(1);
  const [reject, setReject] = useState(false);

  useEffect(() => { setInv(source ? buildInvoice(source) : null); setPage(1); }, [source?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const issues = useMemo(() => {
    if (!inv) return [];
    const out: string[] = [];
    const t = invoiceTotals(inv);
    if (!GSTIN_RE.test(inv.vendorGstin)) out.push("Vendor GSTIN format is invalid.");
    if (!PAN_RE.test(inv.vendorPan)) out.push("Vendor PAN format is invalid.");
    else if (inv.vendorGstin.slice(2, 12) !== inv.vendorPan) out.push("Vendor PAN does not match GSTIN.");
    if (!inv.invoiceNumber.trim()) out.push("Invoice number is required.");
    if (Math.abs(t.lineTax - t.headerTax) > 1) out.push(`Header tax (${fmtINR(t.headerTax)}) differs from line-item tax (${fmtINR(t.lineTax)}).`);
    if (inv.igst > 0 && (inv.cgst > 0 || inv.sgst > 0)) out.push("IGST and CGST/SGST both present — check place of supply.");
    return out;
  }, [inv]);

  if (!inv || !source) {
    return (<><PageHeader title="Invoice Processing" description="Review extracted invoice data." /><EmptyState title="No extracted invoices for this company" hint="Upload or process documents in the Document Inbox." /></>);
  }

  const t = invoiceTotals(inv);
  const set = <K extends keyof Invoice>(k: K, v: Invoice[K]) => setInv({ ...inv, [k]: v });
  const setNum = (k: "cgst" | "sgst" | "igst" | "cess" | "roundOff", v: string) => set(k, Number(v) || 0);
  const setMap = (k: keyof Invoice["mapping"], v: string) => setInv({ ...inv, mapping: { ...inv.mapping, [k]: v } });
  const setLine = (id: string, k: string, v: string) =>
    set("lineItems", inv.lineItems.map((l) => (l.id === id ? { ...l, [k]: ["qty", "unitPrice", "taxRate"].includes(k) ? Number(v) || 0 : v } : l)));
  const lowConf = inv.mapping.confidence < 80;

  const setDocStatus = (status: "Approved" | "Needs Review" | "Failed" | "Exported") =>
    s.setDocuments((d) => d.map((x) => (x.id === source.id ? { ...x, status } : x)));

  const approve = () => {
    if (issues.length) return toast.error("Resolve validation messages before approving.");
    setInv({ ...inv, status: "Approved" });
    setDocStatus("Approved");
    s.log({ module: "Invoice Processing", action: "Approved", recordId: inv.invoiceNumber, previous: "Status: Draft", next: "Status: Approved" });
    toast.success("Invoice data approved");
  };
  const approveMapping = () => {
    setMap("status", "Approved");
    s.log({ module: "Invoice Processing", action: "Approved", recordId: inv.invoiceNumber, previous: "Mapping: Suggested", next: `Ledger: ${inv.mapping.expenseLedger}` });
    toast.success("Ledger mapping approved and saved to vendor history");
  };
  const toReview = () => {
    s.setReviews((r) => [{ id: `RV-${Date.now().toString().slice(-5)}`, companyId: s.companyId, type: lowConf ? "Low Confidence Extraction" : "Invoice Data Review", description: `${inv.vendorName} · ${inv.invoiceNumber}`, assignedTo: "Unassigned", createdAt: new Date().toISOString().slice(0, 10), priority: lowConf ? "High" : "Medium", status: "Open", comments: [] }, ...r]);
    setInv({ ...inv, status: "Needs Review" });
    setDocStatus("Needs Review");
    toast.success("Sent to Review Queue");
  };
  const doExport = (target: "Tally" | "Zoho Books") => {
    if (inv.status !== "Approved" || inv.mapping.status !== "Approved") return toast.error("Approve invoice data and ledger mapping before export.");
    const rows = inv.lineItems.map((l) => ({
      Date: inv.invoiceDate, VoucherType: "Purchase", InvoiceNo: inv.invoiceNumber, Party: inv.mapping.vendorLedger, PartyGSTIN: inv.vendorGstin,
      Ledger: inv.mapping.expenseLedger, Description: l.description, HSN: l.hsn, Qty: l.qty, Rate: l.unitPrice, Taxable: l.qty * l.unitPrice, TaxRate: l.taxRate,
    }));
    const n = exportFile("CSV", `${target.replace(" ", "")}_${inv.invoiceNumber.replace(/\//g, "-")}`, rows);
    if (n) {
      s.addExport({ name: `Invoice ${inv.invoiceNumber}`, type: "Purchase Vouchers", format: "CSV", records: n, status: "Generated", target });
      setDocStatus("Exported");
      toast.success(`CSV for ${target} downloaded (${n} rows)`);
    }
  };

  return (
    <>
      <PageHeader
        title="Invoice Processing"
        description="Review extracted fields, line items and ledger mapping before export."
        actions={
          <select aria-label="Select document" className={selectCls} value={docId} onChange={(e) => navigate({ to: "/invoices", search: { doc: e.target.value } })}>
            {candidates.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.status}</option>)}
          </select>
        }
      />
      <DemoNotice>Extraction results are mock data. No OCR or AI service is connected.</DemoNotice>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        Review status: <StatusBadge status={inv.status} /> Mapping: <StatusBadge status={inv.mapping.status} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(320px,2fr)_3fr]">
        <Section title="Document Preview" actions={
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(50, z - 10))}><ZoomOut /></Button>
            <span className="num w-10 text-center text-xs">{zoom}%</span>
            <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(200, z + 10))}><ZoomIn /></Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Previous page" disabled={page === 1} onClick={() => setPage(1)}><ChevronLeft /></Button>
            <span className="text-xs">{page}/2</span>
            <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Next page" disabled={page === 2} onClick={() => setPage(2)}><ChevronRight /></Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Download original" onClick={() => toast.info("Original files will be downloadable once document storage is connected.")}><Download /></Button>
          </div>
        }>
          <div className="h-[560px] overflow-auto rounded bg-muted p-4">
            <div className="mx-auto origin-top rounded border bg-card p-6 text-xs shadow-sm" style={{ width: `${(zoom / 100) * 420}px`, fontSize: `${(zoom / 100) * 11}px` }}>
              {page === 1 ? (
                <>
                  <div className="mb-3 flex justify-between border-b pb-2"><b className="text-sm">{inv.vendorName}</b><span>TAX INVOICE</span></div>
                  <p>GSTIN: {inv.vendorGstin}</p><p>Invoice No: {inv.invoiceNumber}</p><p>Date: {inv.invoiceDate}</p><p>PO: {inv.poNumber}</p>
                  <p className="mt-2">Bill to GSTIN: {inv.buyerGstin}</p>
                  <table className="mt-3 w-full"><tbody>{inv.lineItems.map((l) => <tr key={l.id} className="border-t"><td className="py-1">{l.description}</td><td className="text-right">{fmtINR(l.qty * l.unitPrice)}</td></tr>)}</tbody></table>
                  <p className="mt-3 text-right font-semibold">Total {fmtINR(t.total)}</p>
                </>
              ) : <p className="text-muted-foreground">Page 2 — Terms & bank details (sample)</p>}
            </div>
          </div>
        </Section>

        <div className="flex flex-col gap-4">
          {issues.length > 0 && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              <b>Validation</b>
              <ul className="mt-1 list-disc pl-5">{issues.map((i) => <li key={i}>{i}</li>)}</ul>
            </div>
          )}
          <Section title="Extracted Fields">
            <div className="grid gap-3 md:grid-cols-3">
              <Field label="Vendor Name"><Input value={inv.vendorName} onChange={(e) => set("vendorName", e.target.value)} /></Field>
              <Field label="Invoice Number"><Input value={inv.invoiceNumber} onChange={(e) => set("invoiceNumber", e.target.value)} /></Field>
              <Field label="Invoice Date"><Input type="date" value={inv.invoiceDate} onChange={(e) => set("invoiceDate", e.target.value)} /></Field>
              <Field label="Vendor GSTIN"><Input className="num" value={inv.vendorGstin} onChange={(e) => set("vendorGstin", e.target.value.toUpperCase())} /></Field>
              <Field label="Vendor PAN"><Input className="num" value={inv.vendorPan} onChange={(e) => set("vendorPan", e.target.value.toUpperCase())} /></Field>
              <Field label="Buyer GSTIN"><Input className="num" value={inv.buyerGstin} onChange={(e) => set("buyerGstin", e.target.value.toUpperCase())} /></Field>
              <Field label="Purchase Order No."><Input value={inv.poNumber} onChange={(e) => set("poNumber", e.target.value)} /></Field>
              <Field label="Taxable Amount"><Input className="num" readOnly value={t.taxable.toFixed(2)} /></Field>
              <Field label="CGST"><Input className="num" type="number" value={inv.cgst} onChange={(e) => setNum("cgst", e.target.value)} /></Field>
              <Field label="SGST"><Input className="num" type="number" value={inv.sgst} onChange={(e) => setNum("sgst", e.target.value)} /></Field>
              <Field label="IGST"><Input className="num" type="number" value={inv.igst} onChange={(e) => setNum("igst", e.target.value)} /></Field>
              <Field label="Cess"><Input className="num" type="number" value={inv.cess} onChange={(e) => setNum("cess", e.target.value)} /></Field>
              <Field label="Round-off"><Input className="num" type="number" step="0.01" value={inv.roundOff} onChange={(e) => setNum("roundOff", e.target.value)} /></Field>
              <Field label="Total Invoice Value"><Input className="num font-semibold" readOnly value={t.total.toFixed(2)} /></Field>
            </div>
          </Section>

          <Section title="Line Items" actions={<Button size="sm" variant="outline" className="h-7" onClick={() => set("lineItems", [...inv.lineItems, { id: `li-${Date.now()}`, description: "", hsn: "", qty: 1, unit: "NOS", unitPrice: 0, taxRate: 18 }])}><Plus />Add line</Button>}>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground"><tr>{["Description", "HSN/SAC", "Qty", "Unit", "Unit Price", "Tax %", "Tax Amt", "Line Total", ""].map((h) => <th key={h} className="px-1 py-1 text-left font-medium">{h}</th>)}</tr></thead>
                <tbody>
                  {inv.lineItems.map((l) => {
                    const base = l.qty * l.unitPrice; const tax = (base * l.taxRate) / 100;
                    return (
                      <tr key={l.id} className="border-t">
                        <td className="p-1"><Input className="h-8 min-w-40" value={l.description} onChange={(e) => setLine(l.id, "description", e.target.value)} /></td>
                        <td className="p-1"><Input className="num h-8 w-20" value={l.hsn} onChange={(e) => setLine(l.id, "hsn", e.target.value)} /></td>
                        <td className="p-1"><Input className="num h-8 w-16" type="number" value={l.qty} onChange={(e) => setLine(l.id, "qty", e.target.value)} /></td>
                        <td className="p-1"><Input className="h-8 w-16" value={l.unit} onChange={(e) => setLine(l.id, "unit", e.target.value)} /></td>
                        <td className="p-1"><Input className="num h-8 w-24" type="number" value={l.unitPrice} onChange={(e) => setLine(l.id, "unitPrice", e.target.value)} /></td>
                        <td className="p-1"><select className="h-8 rounded border border-input bg-background px-1 text-sm" value={l.taxRate} onChange={(e) => setLine(l.id, "taxRate", e.target.value)}>{[0, 5, 12, 18, 28].map((r) => <option key={r} value={r}>{r}%</option>)}</select></td>
                        <td className="num p-1 text-right">{fmtINR(tax)}</td>
                        <td className="num p-1 text-right">{fmtINR(base + tax)}</td>
                        <td className="p-1"><Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Remove line" onClick={() => set("lineItems", inv.lineItems.filter((x) => x.id !== l.id))}><Trash2 /></Button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Ledger Mapping" actions={<ConfidenceBadge value={inv.mapping.confidence} />}>
            {lowConf && (
              <div className="mb-3 flex items-center gap-2 rounded border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                <AlertTriangle className="h-4 w-4" /> Confidence is below 80%. Manual review is recommended before approval.
              </div>
            )}
            <div className="grid gap-3 md:grid-cols-3">
              <Field label="Suggested Expense Ledger"><select className={selectCls} value={inv.mapping.expenseLedger} onChange={(e) => setMap("expenseLedger", e.target.value)}>{EXPENSE_LEDGERS.map((x) => <option key={x}>{x}</option>)}</select></Field>
              <Field label="Vendor Ledger"><Input value={inv.mapping.vendorLedger} onChange={(e) => setMap("vendorLedger", e.target.value)} /></Field>
              <Field label="Parent Group"><select className={selectCls} value={inv.mapping.parentGroup} onChange={(e) => setMap("parentGroup", e.target.value)}>{PARENT_GROUPS.map((x) => <option key={x}>{x}</option>)}</select></Field>
              <Field label="Tax Ledger"><select className={selectCls} value={inv.mapping.taxLedger} onChange={(e) => setMap("taxLedger", e.target.value)}>{["Input CGST / SGST", "Input IGST", "Input Cess"].map((x) => <option key={x}>{x}</option>)}</select></Field>
              <Field label="Mapping Source"><Input readOnly value={inv.mapping.source} /></Field>
              <Field label="Mapping Status"><div className="pt-1.5"><StatusBadge status={inv.mapping.status} /></div></Field>
            </div>
          </Section>

          <div className="sticky bottom-0 flex flex-wrap gap-2 rounded-md border bg-card p-3">
            <Button variant="outline" onClick={() => { setInv({ ...inv, status: inv.status === "Approved" ? "Approved" : "Draft" }); toast.success("Draft saved"); }}><Save />Save Draft</Button>
            <Button onClick={approve}><Check />Approve Data</Button>
            <Button variant="secondary" onClick={approveMapping}><Check />Approve Ledger Mapping</Button>
            <Button variant="outline" onClick={toReview}><Send />Send to Review</Button>
            <Button variant="outline" onClick={() => doExport("Tally")}>Export for Tally</Button>
            <Button variant="outline" onClick={() => doExport("Zoho Books")}>Export for Zoho</Button>
            <Button variant="destructive" onClick={() => setReject(true)}><X />Reject</Button>
          </div>
        </div>
      </div>

      <AlertDialog open={reject} onOpenChange={setReject}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reject this document?</AlertDialogTitle>
            <AlertDialogDescription>The document will be marked as Failed and removed from the processing workspace.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => { setDocStatus("Failed"); s.log({ module: "Invoice Processing", action: "Rejected", recordId: inv.invoiceNumber, previous: inv.status, next: "Rejected" }); toast.success("Document rejected"); }}>Reject</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
