import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { PageHeader, StatusBadge, KV, EmptyState } from "@/components/pal/common";
import { DataTable, FilterSelect } from "@/components/pal/data-table";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useCompany } from "@/lib/store";
import { useVouchers } from "@/lib/queries/vouchers";
import { voucherTypeLabel, type Voucher, type VoucherSourceType } from "@/lib/api/vouchers";
import { fmtDate, fmtMoney } from "@/lib/format";
import { seo } from "@/lib/seo";

export const Route = createFileRoute("/review")({
  head: () => seo("Posted to Tally", "Every voucher posted to Tally, in one place."),
  component: PostedPage,
});

const SOURCE_LABEL: Record<VoucherSourceType, string> = {
  INVOICE: "Invoice",
  BANK_TRANSACTION: "Bank",
  MANUAL: "Manual",
};

/** Distinct ledger names on a voucher, in line order. */
const ledgerNames = (v: Voucher) =>
  Array.from(new Set(v.lines.map((l) => l.ledgerName).filter(Boolean))).join(", ");

function PostedPage() {
  const company = useCompany();
  const { data: posted = [] } = useVouchers(company?.id, { status: "POSTED" });
  const [source, setSource] = useState("All");
  const [openId, setOpenId] = useState<string | null>(null);
  const open = posted.find((v) => v.id === openId) ?? null;

  if (!company) {
    return (
      <>
        <PageHeader title="Posted to Tally" description="Vouchers posted to Tally." />
        <div className="rounded-md border bg-card p-4">
          <EmptyState
            title="No company selected"
            hint="Create a company to see its posted entries."
          />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Posted to Tally"
        description={`Every voucher posted to Tally for ${company.displayName}.`}
      />
      <DataTable
        rows={posted.filter((v) => source === "All" || SOURCE_LABEL[v.sourceType] === source)}
        rowKey={(r) => r.id}
        searchKeys={(r) => `${r.voucherNumber} ${r.postingReference ?? ""} ${ledgerNames(r)}`}
        onRowClick={(r) => setOpenId(r.id)}
        toolbar={
          <FilterSelect
            label="Source"
            value={source}
            onChange={setSource}
            options={["Invoice", "Bank", "Manual"]}
          />
        }
        columns={[
          {
            key: "voucherNumber",
            header: "Voucher",
            cell: (r) => <span className="num">{r.voucherNumber}</span>,
          },
          { key: "voucherDate", header: "Date", cell: (r) => fmtDate(r.voucherDate) },
          { key: "voucherType", header: "Type", cell: (r) => voucherTypeLabel(r.voucherType) },
          { key: "source", header: "Source", cell: (r) => SOURCE_LABEL[r.sourceType] },
          { key: "ledgers", header: "Posted ledgers", cell: (r) => ledgerNames(r) },
          {
            key: "amount",
            header: "Amount",
            align: "right",
            sortValue: (r) => r.totalDebit,
            cell: (r) => fmtMoney(r.totalDebit),
          },
          {
            key: "postedAt",
            header: "Posted",
            cell: (r) => (r.postedAt ? fmtDate(r.postedAt) : "—"),
          },
          { key: "ref", header: "Tally ref", cell: (r) => r.postingReference ?? "—" },
        ]}
      />
      <Sheet open={!!open} onOpenChange={(o) => !o && setOpenId(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{open?.voucherNumber}</SheetTitle>
          </SheetHeader>
          {open && (
            <div className="mt-4 space-y-4">
              <div>
                <KV k="Date" v={fmtDate(open.voucherDate)} />
                <KV k="Type" v={voucherTypeLabel(open.voucherType)} />
                <KV k="Source" v={SOURCE_LABEL[open.sourceType]} />
                <KV k="Status" v={<StatusBadge status="Posted" />} />
                <KV k="Posted" v={open.postedAt ? fmtDate(open.postedAt) : "—"} />
                <KV k="Tally reference" v={open.postingReference ?? "—"} />
              </div>
              <div>
                <h3 className="mb-2 text-sm font-semibold">Ledger entries</h3>
                <ul className="divide-y rounded border text-sm">
                  {open.lines.map((l) => (
                    <li key={l.id} className="flex justify-between gap-2 px-3 py-2">
                      <span>
                        {l.ledgerName} <span className="text-muted-foreground">({l.side})</span>
                      </span>
                      <span className="num">{fmtMoney(l.amount)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
