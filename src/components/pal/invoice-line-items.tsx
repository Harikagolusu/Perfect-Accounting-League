/**
 * Editable invoice line items.
 *
 * Rows are persisted through the existing line-item API. Taxable and line-total
 * amounts are computed from quantity, unit rate and GST rate when the row is
 * saved, so the stored figures match what the table shows. Nothing else on the
 * invoice is recalculated.
 */
import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Section, EmptyState } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtMoney } from "@/lib/format";
import type { InvoiceLineItemWrite } from "@/lib/api/invoices";
import {
  useCreateLineItem,
  useDeleteLineItem,
  useInvoiceLineItems,
  useUpdateLineItem,
} from "@/lib/queries/invoices";
import { toast } from "sonner";

const GST_RATES = ["0", "5", "12", "18", "28"];
const cellCls = "h-9";

type Draft = {
  description: string;
  hsn_sac: string;
  quantity: string;
  unit_rate: string;
  gst_rate: string;
};

/** Parse a decimal string to a number; blank or invalid becomes 0. */
const num = (v: string) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

/** Taxable, tax and line total from a draft, each rounded to 2 decimals. */
function compute(d: Draft) {
  const taxable = num(d.quantity) * num(d.unit_rate);
  const tax = (taxable * num(d.gst_rate)) / 100;
  const total = taxable + tax;
  return {
    taxable: taxable.toFixed(2),
    tax: tax.toFixed(2),
    total: total.toFixed(2),
  };
}

const money = (v: string) => `₹${fmtMoney(v)}`;

export function InvoiceLineItemsPanel({
  companyId,
  invoiceId,
}: {
  companyId: string;
  invoiceId: string;
}) {
  const { data, isPending } = useInvoiceLineItems(companyId, invoiceId);
  const createItem = useCreateLineItem(companyId, invoiceId);
  const updateItem = useUpdateLineItem(companyId, invoiceId);
  const deleteItem = useDeleteLineItem(companyId, invoiceId);

  const items = useMemo(() => data ?? [], [data]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});

  // Mirror server rows into local drafts. Keyed by id, so an in-flight edit on
  // one row is not clobbered by another row's refetch.
  useEffect(() => {
    setDrafts((prev) => {
      const next: Record<string, Draft> = {};
      for (const it of items) {
        next[it.id] = prev[it.id] ?? {
          description: it.description ?? "",
          hsn_sac: it.hsn_sac ?? "",
          quantity: it.quantity ?? "",
          unit_rate: it.unit_rate ?? "",
          gst_rate: it.gst_rate ?? "0",
        };
      }
      return next;
    });
  }, [items]);

  const setField = (id: string, key: keyof Draft, value: string) =>
    setDrafts((d) => ({ ...d, [id]: { ...d[id], [key]: value } }));

  const save = async (id: string) => {
    const d = drafts[id];
    if (!d) return;
    const c = compute(d);
    const body: InvoiceLineItemWrite = {
      description: d.description || "Item",
      hsn_sac: d.hsn_sac || null,
      quantity: d.quantity || null,
      unit_rate: d.unit_rate || null,
      gst_rate: d.gst_rate || null,
      taxable_amount: d.quantity && d.unit_rate ? c.taxable : null,
      total_amount: d.quantity && d.unit_rate ? c.total : null,
    };
    try {
      await updateItem.mutateAsync({ id, body });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save line item");
    }
  };

  const addLine = async () => {
    try {
      await createItem.mutateAsync({ description: "Item", gst_rate: "0" });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not add line item");
    }
  };

  const remove = async (id: string) => {
    try {
      await deleteItem.mutateAsync(id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete line item");
    }
  };

  return (
    <Section
      title="Line Items"
      actions={
        <Button size="sm" variant="outline" onClick={addLine} disabled={createItem.isPending}>
          <Plus />
          Add line
        </Button>
      }
    >
      {isPending ? (
        <p className="text-sm text-muted-foreground">Loading line items…</p>
      ) : items.length === 0 ? (
        <EmptyState
          title="No line items yet"
          hint="Add a line, or they arrive automatically when the document is extracted."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr className="text-left">
                <th className="px-2 py-1.5 font-medium">Description</th>
                <th className="px-2 py-1.5 font-medium">HSN/SAC</th>
                <th className="px-2 py-1.5 font-medium">Qty</th>
                <th className="px-2 py-1.5 font-medium">Unit Price</th>
                <th className="px-2 py-1.5 font-medium">Tax %</th>
                <th className="px-2 py-1.5 text-right font-medium">Tax Amt</th>
                <th className="px-2 py-1.5 text-right font-medium">Line Total</th>
                <th className="px-2 py-1.5"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const d = drafts[it.id];
                if (!d) return null;
                const c = compute(d);
                return (
                  <tr key={it.id} className="border-t">
                    <td className="px-2 py-1.5">
                      <Input
                        className={cellCls + " min-w-44"}
                        aria-label="Description"
                        value={d.description}
                        onChange={(e) => setField(it.id, "description", e.target.value)}
                        onBlur={() => save(it.id)}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <Input
                        className={cellCls + " num w-28"}
                        aria-label="HSN or SAC"
                        value={d.hsn_sac}
                        onChange={(e) => setField(it.id, "hsn_sac", e.target.value)}
                        onBlur={() => save(it.id)}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <Input
                        className={cellCls + " num w-20"}
                        inputMode="decimal"
                        aria-label="Quantity"
                        value={d.quantity}
                        onChange={(e) => setField(it.id, "quantity", e.target.value)}
                        onBlur={() => save(it.id)}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <Input
                        className={cellCls + " num w-24"}
                        inputMode="decimal"
                        aria-label="Unit price"
                        value={d.unit_rate}
                        onChange={(e) => setField(it.id, "unit_rate", e.target.value)}
                        onBlur={() => save(it.id)}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <select
                        aria-label="Tax rate"
                        className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                        value={d.gst_rate}
                        onChange={(e) => {
                          setField(it.id, "gst_rate", e.target.value);
                        }}
                        onBlur={() => save(it.id)}
                      >
                        {GST_RATES.map((r) => (
                          <option key={r} value={r}>
                            {r}%
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="num px-2 py-1.5 text-right tabular-nums">{money(c.tax)}</td>
                    <td className="num px-2 py-1.5 text-right font-medium tabular-nums">
                      {money(c.total)}
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-8 w-8 text-destructive"
                        aria-label="Delete line"
                        onClick={() => remove(it.id)}
                        disabled={deleteItem.isPending}
                      >
                        <Trash2 />
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 px-2 text-xs text-muted-foreground">
            Tax Amount and Line Total are computed from Qty, Unit Price and Tax %, then stored with
            the line.
          </p>
        </div>
      )}
    </Section>
  );
}
