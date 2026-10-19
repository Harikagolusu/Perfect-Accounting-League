import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Download, Check } from "lucide-react";
import { PageHeader, StatusBadge, ConfidenceBadge, Field, DemoNotice, Section, KV } from "@/components/pal/common";
import { DataTable, FilterSelect } from "@/components/pal/data-table";
import { FileUploadDropzone } from "@/components/pal/dropzone";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter } from "@/components/ui/sheet";
import { useScoped, useStore } from "@/lib/store";
import { BANKS, EXPENSE_LEDGERS } from "@/lib/mock-data";
import { fmtDate, fmtINR, mask } from "@/lib/format";
import { exportFile } from "@/lib/export";
import type { BankTransaction } from "@/lib/types";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/bank")({
  head: () => seo("Bank Statements", "Upload bank statements, clean narrations and map transactions to ledgers."),
  component: BankPage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";
const LEDGERS = [...EXPENSE_LEDGERS, "Sundry Debtors", "Bank Charges", "Suspense"];

function BankPage() {
  const s = useStore();
  const tx = useScoped(s.bankTx);
  const [bank, setBank] = useState("HDFC");
  const [account, setAccount] = useState("50200012345678");
  const [from, setFrom] = useState("2026-09-18");
  const [to, setTo] = useState("2026-10-18");
  const [file, setFile] = useState<File | null>(null);
  const [type, setType] = useState("All");
  const [st, setSt] = useState("All");
  const [dFrom, setDFrom] = useState("");
  const [dTo, setDTo] = useState("");
  const [open, setOpen] = useState<BankTransaction | null>(null);

  const update = (ids: string[], patch: Partial<BankTransaction>) => s.setBankTx((all) => all.map((t) => (ids.includes(t.id) ? { ...t, ...patch } : t)));

  const rows = tx.filter((t) =>
    (type === "All" || (type === "Debit" ? t.debit > 0 : t.credit > 0)) &&
    (st === "All" || t.status === st) && (!dFrom || t.date >= dFrom) && (!dTo || t.date <= dTo));

  const upload = () => {
    if (!file) return toast.error("Choose a statement file first.");
    if (from > to) return toast.error("Start date must be before end date.");
    toast.success(`${file.name} received`, { description: `${bank} ${mask(account)} · parsing is simulated in this demo.` });
    setFile(null);
  };

  return (
    <>
      <PageHeader title="Bank Statements" description="Clean narrations, identify counterparties and map transactions to ledgers."
        actions={<Button variant="outline" onClick={() => { const n = exportFile("CSV", `bank_transactions_${bank}`, rows.map((r) => ({ Date: r.date, ValueDate: r.valueDate, Narration: r.cleanNarration, Reference: r.reference, Debit: r.debit, Credit: r.credit, Balance: r.balance, Ledger: r.ledger, Status: r.status }))); toast.success(`Exported ${n} transactions`); }}><Download />Export CSV</Button>} />
      <DemoNotice>Sample transactions. No bank connection is made — statements are parsed by the future backend.</DemoNotice>

      <Section title="Upload Statement" className="mb-4">
        <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Company"><select className={selectCls} value={s.companyId} onChange={(e) => s.setCompanyId(e.target.value)}>{s.companies.map((c) => <option key={c.id} value={c.id}>{c.displayName}</option>)}</select></Field>
            <Field label="Bank"><select className={selectCls} value={bank} onChange={(e) => setBank(e.target.value)}>{BANKS.map((b) => <option key={b}>{b}</option>)}</select></Field>
            <Field label="Bank Account"><select className={selectCls} value={account} onChange={(e) => setAccount(e.target.value)}>{["50200012345678", "00981234567890"].map((a) => <option key={a} value={a}>Current A/c {mask(a)}</option>)}</select></Field>
            <div />
            <Field label="Statement Start"><Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
            <Field label="Statement End"><Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
          </div>
          <div className="flex flex-col gap-2">
            <FileUploadDropzone multiple={false} accept=".pdf,.xlsx,.xls,.csv" hint="PDF or Excel statement" onFiles={(f) => setFile(f[0])} />
            {file && <p className="text-xs text-muted-foreground">Selected: {file.name}</p>}
            <Button onClick={upload} disabled={!file}>Upload statement</Button>
          </div>
        </div>
      </Section>

      <DataTable
        rows={rows}
        rowKey={(r) => r.id}
        pageSize={12}
        searchKeys={(r) => `${r.rawNarration} ${r.cleanNarration} ${r.reference} ${r.counterparty}`}
        selectable
        bulkActions={(ids, clear) => <Button size="sm" variant="outline" className="h-7" onClick={() => { const ok = ids.filter((id) => tx.find((t) => t.id === id)?.ledger); update(ok, { status: "Approved" }); toast.success(`${ok.length} mapping(s) approved`, { description: ids.length - ok.length ? `${ids.length - ok.length} skipped — no ledger set.` : undefined }); clear(); }}><Check />Bulk approve</Button>}
        onRowClick={setOpen}
        toolbar={<>
          <Input type="date" aria-label="From date" className="h-9 w-36" value={dFrom} onChange={(e) => setDFrom(e.target.value)} />
          <Input type="date" aria-label="To date" className="h-9 w-36" value={dTo} onChange={(e) => setDTo(e.target.value)} />
          <FilterSelect label="Type" value={type} onChange={setType} options={["Debit", "Credit"]} />
          <FilterSelect label="Status" value={st} onChange={setSt} options={["Approved", "Suggested", "Unmapped"]} />
        </>}
        columns={[
          { key: "date", header: "Txn Date", cell: (r) => fmtDate(r.date) },
          { key: "valueDate", header: "Value Date", cell: (r) => fmtDate(r.valueDate) },
          { key: "rawNarration", header: "Raw Narration", cell: (r) => <span className="num block max-w-56 truncate text-xs" title={r.rawNarration}>{r.rawNarration}</span> },
          { key: "cleanNarration", header: "Cleaned Narration" },
          { key: "reference", header: "Ref No.", cell: (r) => <span className="num text-xs">{r.reference}</span> },
          { key: "debit", header: "Debit", align: "right", sortValue: (r) => r.debit, cell: (r) => (r.debit ? fmtINR(r.debit) : "") },
          { key: "credit", header: "Credit", align: "right", sortValue: (r) => r.credit, cell: (r) => (r.credit ? fmtINR(r.credit) : "") },
          { key: "balance", header: "Balance", align: "right", sortValue: (r) => r.balance, cell: (r) => fmtINR(r.balance) },
          { key: "counterparty", header: "Counterparty", cell: (r) => r.counterparty || "—" },
          { key: "ledger", header: "Ledger", cell: (r) => r.ledger || "—" },
          { key: "confidence", header: "Conf.", sortValue: (r) => r.confidence, cell: (r) => (r.confidence ? <ConfidenceBadge value={r.confidence} /> : "—") },
          { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
        ]}
      />

      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader><SheetTitle>Transaction detail</SheetTitle></SheetHeader>
          {open && <TxEditor key={open.id} t={open} onSave={(patch) => { update([open.id], patch); toast.success(patch.status === "Approved" ? "Mapping approved" : "Transaction updated"); setOpen(null); }} />}
        </SheetContent>
      </Sheet>
    </>
  );
}

function TxEditor({ t, onSave }: { t: BankTransaction; onSave: (p: Partial<BankTransaction>) => void }) {
  const [clean, setClean] = useState(t.cleanNarration);
  const [ledger, setLedger] = useState(t.ledger);
  const [cp, setCp] = useState(t.counterparty);
  return (
    <div className="mt-4 space-y-4">
      <div><KV k="Date" v={fmtDate(t.date)} /><KV k="Reference" v={t.reference} /><KV k="Amount" v={t.debit ? `Dr ${fmtINR(t.debit)}` : `Cr ${fmtINR(t.credit)}`} /><KV k="Status" v={<StatusBadge status={t.status} />} /></div>
      <Field label="Original narration"><div className="num rounded border bg-muted p-2 text-xs">{t.rawNarration}</div></Field>
      <Field label="Cleaned narration"><Textarea rows={2} value={clean} onChange={(e) => setClean(e.target.value)} /></Field>
      <Field label="Counterparty"><Input value={cp} onChange={(e) => setCp(e.target.value)} /></Field>
      <Field label="Ledger"><select className={selectCls} value={ledger} onChange={(e) => setLedger(e.target.value)}><option value="">— Select ledger —</option>{LEDGERS.map((l) => <option key={l}>{l}</option>)}</select></Field>
      <SheetFooter className="gap-2">
        <Button variant="outline" onClick={() => onSave({ cleanNarration: clean, ledger, counterparty: cp, status: ledger ? "Suggested" : "Unmapped" })}>Save</Button>
        <Button disabled={!ledger} onClick={() => onSave({ cleanNarration: clean, ledger, counterparty: cp, status: "Approved" })}><Check />Approve mapping</Button>
      </SheetFooter>
    </div>
  );
}
