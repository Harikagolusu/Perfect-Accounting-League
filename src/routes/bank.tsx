import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Download, Plus, Save, Sparkles, Trash2 } from "lucide-react";
import {
  PageHeader,
  StatusBadge,
  Field,
  InfoNotice,
  Section,
  EmptyState,
  KV,
} from "@/components/pal/common";
import { DataTable, FilterSelect, type Column } from "@/components/pal/data-table";
import { FileUploadDropzone } from "@/components/pal/dropzone";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useCompany } from "@/lib/store";
import { fmtDate, fmtMoney, compareMoney } from "@/lib/format";
import { exportFile } from "@/lib/export";
import {
  bankAccountLabel,
  toBankAccountPayload,
  type BankAccountFormValues,
} from "@/lib/api/bankAccounts";
import {
  statusToUiLabel,
  toStatementPayload,
  toTransactionPayload,
  type StatementFormValues,
  type TransactionFormValues,
  type UiBankTransaction,
} from "@/lib/api/bankStatements";
import {
  useBankAccounts,
  useCreateBankAccount,
  useDeactivateBankAccount,
} from "@/lib/queries/bankAccounts";
import {
  useBankStatements,
  useBankTransactions,
  useCreateBankStatement,
  useCreateBankTransaction,
  useDeleteBankStatement,
  useProcessBankStatement,
} from "@/lib/queries/bankStatements";
import { useDocuments, useUploadDocuments } from "@/lib/queries/documents";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/bank")({
  head: () =>
    seo(
      "Bank Statements",
      "Record bank accounts, statement periods and the transactions inside them.",
    ),
  component: BankPage,
});

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";
const dash = (v: string | null | undefined) => (v ? v : "—");

const emptyAccountForm: BankAccountFormValues = {
  bankName: "",
  accountName: "",
  accountNumber: "",
  currency: "INR",
};

const emptyStatementForm: StatementFormValues = {
  bankAccountId: "",
  documentId: "",
  statementStartDate: "",
  statementEndDate: "",
  openingBalance: "",
  closingBalance: "",
};

const emptyTransactionForm: TransactionFormValues = {
  transactionDate: "",
  valueDate: "",
  description: "",
  referenceNumber: "",
  debitAmount: "",
  creditAmount: "",
  balance: "",
};

function BankPage() {
  const company = useCompany();
  const companyId = company?.id;

  const accountsQuery = useBankAccounts(companyId);
  const statementsQuery = useBankStatements(companyId);
  const documentsQuery = useDocuments(companyId);
  const uploadDocuments = useUploadDocuments(companyId);
  const createAccount = useCreateBankAccount(companyId);
  const deactivateAccount = useDeactivateBankAccount(companyId);
  const createStatement = useCreateBankStatement(companyId);
  const createTransaction = useCreateBankTransaction(companyId);
  const deleteStatement = useDeleteBankStatement(companyId);
  const processStatement = useProcessBankStatement(companyId);

  const accounts = useMemo(() => accountsQuery.data ?? [], [accountsQuery.data]);
  const statements = useMemo(() => statementsQuery.data ?? [], [statementsQuery.data]);
  const documents = useMemo(() => documentsQuery.data ?? [], [documentsQuery.data]);

  const [accountForm, setAccountForm] = useState<BankAccountFormValues>(emptyAccountForm);
  const [statementForm, setStatementForm] = useState<StatementFormValues>(emptyStatementForm);
  const [transactionForm, setTransactionForm] =
    useState<TransactionFormValues>(emptyTransactionForm);
  const [file, setFile] = useState<File | null>(null);
  const [statementId, setStatementId] = useState("");
  const [type, setType] = useState("All");
  const [dFrom, setDFrom] = useState("");
  const [dTo, setDTo] = useState("");
  const [open, setOpen] = useState<UiBankTransaction | null>(null);

  // Follow the company: a switch must not leave another company's selection.
  useEffect(() => {
    setStatementId("");
    setStatementForm((f) => ({ ...f, bankAccountId: "" }));
  }, [companyId]);

  // Keep a valid selection as statements come and go.
  useEffect(() => {
    if (statements.length === 0) {
      setStatementId("");
      return;
    }
    if (!statements.some((st) => st.id === statementId)) setStatementId(statements[0].id);
  }, [statements, statementId]);

  const statement = statements.find((st) => st.id === statementId) ?? null;

  const transactionsQuery = useBankTransactions(companyId, statement?.id, {
    dateFrom: dFrom || undefined,
    dateTo: dTo || undefined,
  });
  const transactions = useMemo(() => {
    const rows = transactionsQuery.data ?? [];
    return type === "All" ? rows : rows.filter((t) => t.transactionType === type.toUpperCase());
  }, [transactionsQuery.data, type]);

  const loading =
    accountsQuery.isPending || statementsQuery.isPending || transactionsQuery.isPending;
  const failed = accountsQuery.isError || statementsQuery.isError || transactionsQuery.isError;

  const setAccountField = (k: keyof BankAccountFormValues, v: string) =>
    setAccountForm((f) => ({ ...f, [k]: v }));
  const setStatementField = (k: keyof StatementFormValues, v: string) =>
    setStatementForm((f) => ({ ...f, [k]: v }));
  const setTransactionField = (k: keyof TransactionFormValues, v: string) =>
    setTransactionForm((f) => ({ ...f, [k]: v }));

  const saveAccount = async () => {
    if (!accountForm.bankName.trim()) return toast.error("Bank name is required.");
    if (!accountForm.accountNumber.trim()) return toast.error("Account number is required.");
    try {
      const created = await createAccount.mutateAsync(toBankAccountPayload(accountForm));
      setAccountForm(emptyAccountForm);
      setStatementForm((f) => ({ ...f, bankAccountId: created.id }));
      toast.success("Bank account added", {
        description: `${created.bankName} · ${created.maskedAccountNumber}`,
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not add bank account");
    }
  };

  const deactivate = async (id: string) => {
    try {
      await deactivateAccount.mutateAsync(id);
      toast.success("Bank account deactivated");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not deactivate account");
    }
  };

  const uploadStatement = async () => {
    if (!file) return toast.error("Choose a statement file first.");
    if (!statementForm.bankAccountId) return toast.error("Choose a bank account first.");
    const start = statementForm.statementStartDate;
    const end = statementForm.statementEndDate;
    if (start && end && end < start) {
      return toast.error("Statement end date must not be before the start date.");
    }
    try {
      // The file goes to the Document Inbox first, and the statement points
      // at it. The original stays there whatever happens to the statement.
      const [uploaded] = await uploadDocuments.mutateAsync([file]);
      const created = await createStatement.mutateAsync({
        ...toStatementPayload(statementForm),
        document_id: uploaded.id,
      });
      setFile(null);
      setStatementId(created.id);
      setStatementForm(emptyStatementForm);
      toast.success("Statement uploaded", {
        description: "Stored as a document. No parser has read it — add transactions by hand.",
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not upload statement");
    }
  };

  const createStatementManually = async () => {
    if (!statementForm.bankAccountId) return toast.error("Choose a bank account first.");
    try {
      const created = await createStatement.mutateAsync(toStatementPayload(statementForm));
      setStatementId(created.id);
      setStatementForm(emptyStatementForm);
      toast.success("Statement created");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not create statement");
    }
  };

  const addTransaction = async () => {
    if (!statement) return toast.error("Select a statement first.");
    if (!transactionForm.transactionDate) return toast.error("Transaction date is required.");
    try {
      await createTransaction.mutateAsync({
        statementId: statement.id,
        body: toTransactionPayload(transactionForm),
      });
      setTransactionForm(emptyTransactionForm);
      toast.success("Transaction added");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not add transaction");
    }
  };

  const remove = async () => {
    if (!statement) return;
    try {
      await deleteStatement.mutateAsync(statement.id);
      toast.success("Statement deleted", {
        description: "Its transactions were removed. The document and bank account remain.",
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete statement");
    }
  };

  const runExtraction = async () => {
    if (!statement) return;
    try {
      const result = await processStatement.mutateAsync(statement.id);
      // The backend is honest about having no engine, and the UI says so.
      toast[result.configured ? "success" : "info"](result.message);
      for (const issue of result.issues) toast.warning(issue);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not run extraction");
    }
  };

  const exportCsv = () => {
    const n = exportFile(
      "CSV",
      `bank_transactions_${statement?.id ?? "none"}`,
      transactions.map((t) => ({
        Date: t.transactionDate,
        ValueDate: t.valueDate ?? "",
        Narration: t.description ?? "",
        Reference: t.referenceNumber ?? "",
        Debit: t.debitAmount,
        Credit: t.creditAmount,
        Balance: t.balance ?? "",
        Type: t.transactionType,
      })),
    );
    toast.success(`Exported ${n} transactions`);
  };

  const columns: Column<UiBankTransaction>[] = [
    { key: "transactionDate", header: "Txn Date", cell: (r) => fmtDate(r.transactionDate) },
    {
      key: "valueDate",
      header: "Value Date",
      cell: (r) => (r.valueDate ? fmtDate(r.valueDate) : "—"),
    },
    {
      key: "description",
      header: "Narration",
      cell: (r) =>
        r.description ? (
          <span className="num block max-w-72 truncate text-xs" title={r.description}>
            {r.description}
          </span>
        ) : (
          "—"
        ),
    },
    {
      key: "referenceNumber",
      header: "Ref No.",
      cell: (r) => <span className="num text-xs">{dash(r.referenceNumber)}</span>,
    },
    {
      key: "debitAmount",
      header: "Debit",
      align: "right",
      sortValue: (r) => r.debitAmount,
      sortCompare: (a, b) => compareMoney(a.debitAmount, b.debitAmount),
      cell: (r) => (r.debitAmount === "0.00" ? "" : fmtMoney(r.debitAmount)),
    },
    {
      key: "creditAmount",
      header: "Credit",
      align: "right",
      sortValue: (r) => r.creditAmount,
      sortCompare: (a, b) => compareMoney(a.creditAmount, b.creditAmount),
      cell: (r) => (r.creditAmount === "0.00" ? "" : fmtMoney(r.creditAmount)),
    },
    {
      key: "balance",
      header: "Balance",
      align: "right",
      sortValue: (r) => r.balance ?? "",
      sortCompare: (a, b) => compareMoney(a.balance, b.balance),
      cell: (r) => fmtMoney(r.balance),
    },
    {
      key: "transactionType",
      header: "Type",
      cell: (r) => <StatusBadge status={r.transactionType === "DEBIT" ? "Debit" : "Credit"} />,
    },
  ];

  if (!companyId) {
    return (
      <>
        <PageHeader
          title="Bank Statements"
          description="Record bank accounts, statements and transactions."
        />
        <div className="rounded-md border bg-card p-4">
          <EmptyState
            title="No company selected"
            hint="Create a company first — every bank account and statement belongs to one."
          />
        </div>
      </>
    );
  }

  if (loading) {
    return (
      <>
        <PageHeader
          title="Bank Statements"
          description="Record bank accounts, statements and transactions."
        />
        <div className="rounded-md border bg-card p-4">
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">Loading bank data…</p>
        </div>
      </>
    );
  }

  if (failed) {
    return (
      <>
        <PageHeader
          title="Bank Statements"
          description="Record bank accounts, statements and transactions."
        />
        <div className="rounded-md border border-destructive/40 bg-card p-6">
          <EmptyState
            title="Could not load bank data"
            hint={
              accountsQuery.error instanceof Error
                ? accountsQuery.error.message
                : statementsQuery.error instanceof Error
                  ? statementsQuery.error.message
                  : "The backend is unreachable."
            }
          />
          <div className="flex justify-center">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void accountsQuery.refetch();
                void statementsQuery.refetch();
              }}
            >
              Try again
            </Button>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Bank Statements"
        description="Record bank accounts, statement periods and the transactions inside them."
        actions={
          <Button variant="outline" onClick={exportCsv} disabled={transactions.length === 0}>
            <Download />
            Export CSV
          </Button>
        }
      />
      <InfoNotice>
        Automatic parsing is not configured. Statements are stored against their source document and
        every transaction is entered by hand — nothing here is read from the file, cleaned, or
        mapped to a ledger.
      </InfoNotice>

      <div className="grid gap-4 xl:grid-cols-[minmax(320px,2fr)_3fr]">
        <div className="flex flex-col gap-4">
          <Section title="Upload Statement">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Bank Account">
                <select
                  aria-label="Bank Account"
                  className={selectCls}
                  value={statementForm.bankAccountId}
                  onChange={(e) => setStatementField("bankAccountId", e.target.value)}
                >
                  <option value="">— Select account —</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {bankAccountLabel(a)}
                      {a.isActive ? "" : " (inactive)"}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Link Existing Document">
                <select
                  aria-label="Link Existing Document"
                  className={selectCls}
                  value={statementForm.documentId}
                  onChange={(e) => setStatementField("documentId", e.target.value)}
                >
                  <option value="">— None —</option>
                  {documents.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Statement Start">
                <Input
                  type="date"
                  value={statementForm.statementStartDate}
                  onChange={(e) => setStatementField("statementStartDate", e.target.value)}
                />
              </Field>
              <Field label="Statement End">
                <Input
                  type="date"
                  value={statementForm.statementEndDate}
                  onChange={(e) => setStatementField("statementEndDate", e.target.value)}
                />
              </Field>
              <Field label="Opening Balance">
                <Input
                  className="num"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={statementForm.openingBalance}
                  onChange={(e) => setStatementField("openingBalance", e.target.value)}
                />
              </Field>
              <Field label="Closing Balance">
                <Input
                  className="num"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={statementForm.closingBalance}
                  onChange={(e) => setStatementField("closingBalance", e.target.value)}
                />
              </Field>
            </div>
            <div className="mt-3 flex flex-col gap-2">
              <FileUploadDropzone
                multiple={false}
                accept=".pdf,.xlsx,.xls,.csv"
                hint="PDF or Excel statement"
                onFiles={(f) => setFile(f[0])}
              />
              {file && <p className="text-xs text-muted-foreground">Selected: {file.name}</p>}
              <div className="flex flex-wrap gap-2">
                <Button onClick={uploadStatement} disabled={!file || uploadDocuments.isPending}>
                  Upload statement
                </Button>
                <Button
                  variant="outline"
                  onClick={createStatementManually}
                  disabled={createStatement.isPending}
                >
                  <Plus />
                  Create without a file
                </Button>
              </div>
            </div>
          </Section>

          <Section title="Bank Accounts">
            {accounts.length === 0 ? (
              <EmptyState
                title="No bank accounts yet"
                hint="A statement belongs to a bank account, so add one first."
              />
            ) : (
              <ul className="space-y-2">
                {accounts.map((a) => (
                  <li
                    key={a.id}
                    className="flex items-center justify-between gap-2 rounded border px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {a.bankName}
                        {a.accountName ? ` · ${a.accountName}` : ""}
                      </p>
                      <p className="num text-xs text-muted-foreground">
                        {a.maskedAccountNumber} · {a.currency}
                        {a.isActive ? "" : " · inactive"}
                      </p>
                    </div>
                    {a.isActive && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7"
                        onClick={() => deactivate(a.id)}
                      >
                        Deactivate
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <Field label="Bank Name">
                <Input
                  value={accountForm.bankName}
                  onChange={(e) => setAccountField("bankName", e.target.value)}
                />
              </Field>
              <Field label="Account Name (optional)">
                <Input
                  value={accountForm.accountName}
                  onChange={(e) => setAccountField("accountName", e.target.value)}
                />
              </Field>
              <Field label="Account Number">
                <Input
                  className="num"
                  value={accountForm.accountNumber}
                  onChange={(e) => setAccountField("accountNumber", e.target.value)}
                />
              </Field>
              <Field label="Currency">
                <Input
                  maxLength={3}
                  value={accountForm.currency}
                  onChange={(e) => setAccountField("currency", e.target.value.toUpperCase())}
                />
              </Field>
            </div>
            <Button className="mt-3" onClick={saveAccount} disabled={createAccount.isPending}>
              <Plus />
              Add bank account
            </Button>
            <p className="mt-2 text-xs text-muted-foreground">
              The account number is sent once when the account is created. After that it is only
              ever shown masked.
            </p>
          </Section>
        </div>

        <div className="flex flex-col gap-4">
          <Section title="Statement">
            {statements.length === 0 ? (
              <EmptyState
                title="No bank statements for this company"
                hint="Upload a statement or create one by hand to start recording transactions."
              />
            ) : statement ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    aria-label="Select statement"
                    className={selectCls}
                    value={statementId}
                    onChange={(e) => setStatementId(e.target.value)}
                  >
                    {statements.map((st) => {
                      const owner = accounts.find((a) => a.id === st.bankAccountId);
                      const period = st.statementStartDate
                        ? `${fmtDate(st.statementStartDate)} – ${
                            st.statementEndDate ? fmtDate(st.statementEndDate) : "open"
                          }`
                        : "No period set";
                      return (
                        <option key={st.id} value={st.id}>
                          {owner ? owner.bankName : "Account"} · {period} · {st.status}
                        </option>
                      );
                    })}
                  </select>
                  <StatusBadge status={statusToUiLabel(statement.status)} />
                </div>

                <div className="mt-3 grid gap-x-6 sm:grid-cols-2">
                  <KV k="Opening balance" v={fmtMoney(statement.openingBalance)} />
                  <KV k="Closing balance" v={fmtMoney(statement.closingBalance)} />
                  <KV
                    k="Period"
                    v={
                      statement.statementStartDate
                        ? `${fmtDate(statement.statementStartDate)} – ${
                            statement.statementEndDate
                              ? fmtDate(statement.statementEndDate)
                              : "open"
                          }`
                        : "Not set"
                    }
                  />
                  <KV
                    k="Source document"
                    v={
                      statement.documentId
                        ? (documents.find((d) => d.id === statement.documentId)?.name ?? "—")
                        : "None"
                    }
                  />
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    onClick={runExtraction}
                    disabled={processStatement.isPending}
                  >
                    <Sparkles />
                    Try automatic extraction
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={remove}
                    disabled={deleteStatement.isPending}
                  >
                    <Trash2 />
                    Delete statement
                  </Button>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  Deleting removes the statement and its transactions. The source document and the
                  bank account are kept.
                </p>
              </>
            ) : null}
          </Section>

          <Section title="Add Transaction">
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Transaction Date">
                <Input
                  type="date"
                  value={transactionForm.transactionDate}
                  onChange={(e) => setTransactionField("transactionDate", e.target.value)}
                />
              </Field>
              <Field label="Value Date (optional)">
                <Input
                  type="date"
                  value={transactionForm.valueDate}
                  onChange={(e) => setTransactionField("valueDate", e.target.value)}
                />
              </Field>
              <Field label="Reference (optional)">
                <Input
                  className="num"
                  value={transactionForm.referenceNumber}
                  onChange={(e) => setTransactionField("referenceNumber", e.target.value)}
                />
              </Field>
              <Field label="Narration (optional)">
                <Input
                  value={transactionForm.description}
                  onChange={(e) => setTransactionField("description", e.target.value)}
                />
              </Field>
              <Field label="Debit">
                <Input
                  className="num"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={transactionForm.debitAmount}
                  onChange={(e) => setTransactionField("debitAmount", e.target.value)}
                />
              </Field>
              <Field label="Credit">
                <Input
                  className="num"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={transactionForm.creditAmount}
                  onChange={(e) => setTransactionField("creditAmount", e.target.value)}
                />
              </Field>
              <Field label="Balance (optional)">
                <Input
                  className="num"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={transactionForm.balance}
                  onChange={(e) => setTransactionField("balance", e.target.value)}
                />
              </Field>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Enter a debit or a credit, not both. A transaction with neither is rejected.
            </p>
            <Button
              className="mt-3"
              onClick={addTransaction}
              disabled={!statement || createTransaction.isPending}
            >
              <Save />
              Add transaction
            </Button>
          </Section>

          <div>
            <DataTable
              rows={transactions}
              rowKey={(r) => r.id}
              pageSize={12}
              searchKeys={(r) => `${r.description ?? ""} ${r.referenceNumber ?? ""}`}
              onRowClick={setOpen}
              toolbar={
                <>
                  <Input
                    type="date"
                    aria-label="From date"
                    className="h-9 w-36"
                    value={dFrom}
                    onChange={(e) => setDFrom(e.target.value)}
                  />
                  <Input
                    type="date"
                    aria-label="To date"
                    className="h-9 w-36"
                    value={dTo}
                    onChange={(e) => setDTo(e.target.value)}
                  />
                  <FilterSelect
                    label="Type"
                    value={type}
                    onChange={setType}
                    options={["Debit", "Credit"]}
                  />
                </>
              }
              columns={columns}
            />
            <p className="mt-2 text-xs text-muted-foreground">
              Amounts are decimal text end to end. Debit and credit describe the direction the bank
              recorded — they do not imply an expense or an income.
            </p>
          </div>

          <Section title="Narration Cleaning & Ledger Mapping">
            <EmptyState
              title="Not connected yet"
              hint="Cleaning narrations, identifying counterparties, scoring confidence and mapping transactions to ledgers are all later steps."
            />
          </Section>
        </div>
      </div>

      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Transaction detail</SheetTitle>
          </SheetHeader>
          {open && (
            <div className="mt-4 space-y-4">
              <div>
                <KV k="Date" v={fmtDate(open.transactionDate)} />
                <KV k="Value date" v={open.valueDate ? fmtDate(open.valueDate) : "—"} />
                <KV k="Reference" v={dash(open.referenceNumber)} />
                <KV
                  k="Amount"
                  v={
                    open.debitAmount !== "0.00"
                      ? `Dr ${fmtMoney(open.debitAmount)}`
                      : `Cr ${fmtMoney(open.creditAmount)}`
                  }
                />
                <KV k="Running balance" v={fmtMoney(open.balance)} />
              </div>
              <Field label="Narration as printed by the bank">
                <div className="num rounded border bg-muted p-2 text-xs">
                  {dash(open.description)}
                </div>
              </Field>
              <p className="text-xs text-muted-foreground">
                Stored exactly as entered. Nothing has been cleaned, categorised or reinterpreted.
              </p>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
