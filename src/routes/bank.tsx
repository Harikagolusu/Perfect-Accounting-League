import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Download, Plus, RefreshCw, Save, Sparkles, Trash2 } from "lucide-react";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useCompany } from "@/lib/store";
import { useTallyBankDataSync } from "@/lib/queries/tally";
import { fmtDate, fmtMoney, compareMoney } from "@/lib/format";
import { downloadBlob, exportFile } from "@/lib/export";
import { BankVoucherPanel } from "@/components/pal/bank-voucher";
import { cn } from "@/lib/utils";
import { exportsApi, firstExportError } from "@/lib/api/exports";
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
  useUpdateBankTransaction,
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
/** Indian-grouped amount with a rupee sign; blank stays blank. */
const inr = (v: string | null | undefined) => {
  const grouped = fmtMoney(v);
  return grouped === "—" ? "—" : `₹${grouped}`;
};

/**
 * A readable narration derived from the identified counterparty and the
 * direction the bank recorded. No AI, no stored "cleaned" field: it is the
 * confirmed counterparty phrased for a human. `known` is false when the
 * extractor has not identified a counterparty yet.
 */
const cleanedNarration = (t: UiBankTransaction): { text: string; known: boolean } => {
  const name = t.counterpartyName || t.counterpartyLedger;
  if (!name) return { text: "— counterparty unknown", known: false };
  const isCredit = t.creditAmount !== "0.00";
  return { text: `${isCredit ? "Receipt from" : "Payment to"} ${name}`, known: true };
};

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
  counterpartyName: "",
  counterpartyLedger: "",
  counterpartyAddress: "",
  counterpartyPincode: "",
  counterpartyGstin: "",
  isInterAccountTransfer: false,
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
  const updateTransaction = useUpdateBankTransaction(companyId);

  const tallySyncMutation = useTallyBankDataSync();
  const [tallyCfg, setTallyCfg] = useState({
    tallyCompany: company?.legalName ?? "",
    fromDate: "",
    toDate: "",
  });

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
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<TransactionFormValues>(emptyTransactionForm);
  const [showAddForm, setShowAddForm] = useState(false);
  const [exportingClient, setExportingClient] = useState(false);
  const [tallyOpen, setTallyOpen] = useState(false);
  const [accountsOpen, setAccountsOpen] = useState(false);

  // Follow the company: a switch must not leave another company's selection.
  useEffect(() => {
    setStatementId("");
    setStatementForm((f) => ({ ...f, bankAccountId: "" }));
    setTallyCfg((c) => ({ ...c, tallyCompany: company?.legalName ?? "" }));
  }, [companyId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Pre-fill Tally company name from the loaded company record.
  useEffect(() => {
    if (company?.legalName)
      setTallyCfg((c) => (c.tallyCompany ? c : { ...c, tallyCompany: company.legalName }));
  }, [company?.legalName]);

  // Auto-select the bank account when there is exactly one active account.
  useEffect(() => {
    const active = accounts.filter((a) => a.isActive);
    if (active.length === 1)
      setStatementForm((f) => (f.bankAccountId ? f : { ...f, bankAccountId: active[0].id }));
  }, [accounts]);

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

  const loading = accountsQuery.isPending || statementsQuery.isPending;
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
      toast.success("Statement uploaded — extracting transactions…");
      try {
        const result = await processStatement.mutateAsync(created.id);
        toast[result.configured ? "success" : "info"](result.message);
        for (const issue of result.issues) toast.warning(issue);
      } catch {
        toast.warning("Extraction failed. Use the Extract button to retry.");
      }
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

  const openEdit = (txn: UiBankTransaction) => {
    setEditForm({
      transactionDate: txn.transactionDate,
      valueDate: txn.valueDate ?? "",
      description: txn.description ?? "",
      referenceNumber: txn.referenceNumber ?? "",
      debitAmount: txn.debitAmount !== "0.00" ? txn.debitAmount : "",
      creditAmount: txn.creditAmount !== "0.00" ? txn.creditAmount : "",
      balance: txn.balance ?? "",
      counterpartyName: txn.counterpartyName ?? "",
      counterpartyLedger: txn.counterpartyLedger ?? "",
      counterpartyAddress: txn.counterpartyAddress ?? "",
      counterpartyPincode: txn.counterpartyPincode ?? "",
      counterpartyGstin: txn.counterpartyGstin ?? "",
      isInterAccountTransfer: txn.isInterAccountTransfer,
    });
    setEditing(true);
  };

  const saveEdit = async () => {
    if (!open || !statement) return;
    if (!editForm.transactionDate) return toast.error("Transaction date is required.");
    try {
      const body: Record<string, unknown> = {
        transaction_date: editForm.transactionDate || undefined,
        value_date: editForm.valueDate || undefined,
        description: editForm.description || undefined,
        reference_number: editForm.referenceNumber || undefined,
        debit_amount: editForm.debitAmount || undefined,
        credit_amount: editForm.creditAmount || undefined,
        balance: editForm.balance || undefined,
      };
      const updated = await updateTransaction.mutateAsync({
        statementId: statement.id,
        transactionId: open.id,
        body,
      });
      setOpen(updated);
      setEditing(false);
      toast.success("Transaction updated");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not update transaction");
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

  const exportRows = (rows: UiBankTransaction[]) => {
    const n = exportFile(
      "CSV",
      `bank_transactions_${statement?.id ?? "none"}`,
      rows.map((t) => ({
        Date: t.transactionDate,
        ValueDate: t.valueDate ?? "",
        RawNarration: t.description ?? "",
        CleanedNarration: t.counterpartyName ?? t.counterpartyLedger ?? "",
        Reference: t.referenceNumber ?? "",
        Debit: t.debitAmount,
        Credit: t.creditAmount,
        Balance: t.balance ?? "",
        Type: t.transactionType,
      })),
    );
    toast.success(`Exported ${n} transactions`);
  };

  const exportCsv = () => exportRows(transactions);

  const exportClientBank = async () => {
    if (!companyId) return;
    setExportingClient(true);
    try {
      const options = {
        statementId: statement?.id,
        dateFrom: dFrom || undefined,
        dateTo: dTo || undefined,
      };
      const preview = await exportsApi.bankPreview(companyId, options);
      const blocking = preview.rows === 0 ? firstExportError(preview) : null;
      if (blocking) {
        toast.error(blocking);
        return;
      }
      const file = await exportsApi.bankFile(companyId, options);
      downloadBlob(file.filename, file.blob);
      if (preview.exceptions.length) {
        toast.warning(
          `Downloaded ${preview.vouchers} voucher(s); ${preview.exceptions.length} exception(s) were excluded.`,
        );
      } else if (preview.warnings.length) {
        toast.warning(
          `Downloaded ${preview.vouchers} voucher(s) with ${preview.warnings.length} warning(s).`,
        );
      } else {
        toast.success(
          `Client bank workbook downloaded (${preview.vouchers} voucher(s), ${preview.rows} row(s))`,
        );
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not export the client workbook");
    } finally {
      setExportingClient(false);
    }
  };

  const columns: Column<UiBankTransaction>[] = [
    {
      key: "transactionDate",
      header: "Txn Date",
      className: "num text-xs text-muted-foreground",
      cell: (r) => fmtDate(r.transactionDate),
    },
    {
      key: "valueDate",
      header: "Value",
      className: "num text-xs text-muted-foreground",
      cell: (r) => (r.valueDate ? fmtDate(r.valueDate) : "—"),
    },
    {
      key: "description",
      header: "Raw Narration",
      cell: (r) =>
        r.description ? (
          <span className="num block min-w-[13rem] max-w-sm truncate text-xs" title={r.description}>
            {r.description}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      key: "counterparty",
      header: "Cleaned Narration",
      sortValue: (r) => cleanedNarration(r).text,
      cell: (r) => {
        const { text, known } = cleanedNarration(r);
        return (
          <span
            className={cn(
              "block min-w-[12rem] max-w-sm truncate",
              !known && "text-muted-foreground",
            )}
            title={text}
          >
            {text}
          </span>
        );
      },
    },
    {
      key: "referenceNumber",
      header: "Ref No.",
      className: "num text-xs text-muted-foreground",
      cell: (r) => dash(r.referenceNumber),
    },
    {
      key: "debitAmount",
      header: "Debit",
      align: "right",
      className: "tabular-nums",
      sortValue: (r) => r.debitAmount,
      sortCompare: (a, b) => compareMoney(a.debitAmount, b.debitAmount),
      cell: (r) => (r.debitAmount === "0.00" ? "" : inr(r.debitAmount)),
    },
    {
      key: "creditAmount",
      header: "Credit",
      align: "right",
      className: "tabular-nums text-success",
      sortValue: (r) => r.creditAmount,
      sortCompare: (a, b) => compareMoney(a.creditAmount, b.creditAmount),
      cell: (r) => (r.creditAmount === "0.00" ? "" : inr(r.creditAmount)),
    },
    {
      key: "balance",
      header: "Balance",
      align: "right",
      className: "tabular-nums font-medium",
      sortValue: (r) => r.balance ?? "",
      sortCompare: (a, b) => compareMoney(a.balance, b.balance),
      cell: (r) => inr(r.balance),
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
        description="Clean narrations, identify counterparties and map transactions to ledgers."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={exportCsv} disabled={transactions.length === 0}>
              <Download />
              Export CSV
            </Button>
            <Button
              variant="outline"
              onClick={exportClientBank}
              disabled={exportingClient || transactions.length === 0}
            >
              <Download />
              Client Excel
            </Button>
          </div>
        }
      />
      <InfoNotice>
        Upload a PDF or Excel statement — transactions are extracted automatically. Review and map
        each to a ledger before exporting.
      </InfoNotice>

      <div className="flex flex-col gap-4">
        {/* Upload / create a statement */}
        <Section
          title="Upload Statement"
          actions={
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={() => setAccountsOpen(true)}>
                Bank accounts
              </Button>
              <Button variant="outline" size="sm" onClick={() => setTallyOpen(true)}>
                <RefreshCw />
                Sync from Tally
              </Button>
            </div>
          }
        >
          <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
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
              <Field label="Opening Balance (optional)">
                <Input
                  className="num"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={statementForm.openingBalance}
                  onChange={(e) => setStatementField("openingBalance", e.target.value)}
                />
              </Field>
              <Field label="Closing Balance (optional)">
                <Input
                  className="num"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={statementForm.closingBalance}
                  onChange={(e) => setStatementField("closingBalance", e.target.value)}
                />
              </Field>
            </div>
            <div className="flex flex-col">
              <FileUploadDropzone
                multiple={false}
                accept=".pdf,.xlsx,.xls,.csv"
                hint="PDF or Excel statement"
                onFiles={(f) => setFile(f[0])}
              />
              {file && (
                <p className="mt-2 truncate text-xs text-muted-foreground">Selected: {file.name}</p>
              )}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              className="flex-1 sm:flex-none"
              onClick={uploadStatement}
              disabled={!file || uploadDocuments.isPending}
            >
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
        </Section>

        {/* Statement context bar */}
        <div>
          <Section title="Statement">
            {statements.length === 0 ? (
              <EmptyState
                title="No bank statements yet"
                hint={
                  accounts.length === 0
                    ? "Add a bank account (Bank accounts), then upload a statement above."
                    : "Upload a statement above, enter one by hand, or sync from Tally."
                }
              />
            ) : statement ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    aria-label="Select statement"
                    className={selectCls + " min-w-64 flex-1"}
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
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={runExtraction}
                    disabled={processStatement.isPending}
                  >
                    <Sparkles />
                    Extract
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={remove}
                    disabled={deleteStatement.isPending}
                  >
                    <Trash2 />
                    Delete
                  </Button>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
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
              </>
            ) : null}
          </Section>
        </div>

        {/* Transactions (full width) */}
        <div className="flex flex-col gap-4">
          <div>
            <DataTable
              rows={transactions}
              rowKey={(r) => r.id}
              pageSize={20}
              selectable
              bulkActions={(ids, clear) => (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7"
                  onClick={() => {
                    exportRows(transactions.filter((t) => ids.includes(t.id)));
                    clear();
                  }}
                >
                  <Download />
                  Export selected
                </Button>
              )}
              searchKeys={(r) =>
                `${r.description ?? ""} ${r.counterpartyName ?? ""} ${r.referenceNumber ?? ""}`
              }
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

          <Section title="Add Missing Transaction">
            <Button
              variant="outline"
              size="sm"
              className="mb-3"
              onClick={() => setShowAddForm((v) => !v)}
            >
              {showAddForm ? "Hide form" : "Add a transaction manually"}
            </Button>
            {!showAddForm ? null : (
              <>
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
                  <Field label="Counterparty name (optional)">
                    <Input
                      value={transactionForm.counterpartyName}
                      onChange={(e) => setTransactionField("counterpartyName", e.target.value)}
                    />
                  </Field>
                  <Field label="Counterparty ledger (optional)">
                    <Input
                      value={transactionForm.counterpartyLedger}
                      onChange={(e) => setTransactionField("counterpartyLedger", e.target.value)}
                      placeholder="Must exist in Ledger Master"
                    />
                  </Field>
                  <Field label="Counterparty GSTIN (optional)">
                    <Input
                      className="num"
                      value={transactionForm.counterpartyGstin}
                      onChange={(e) =>
                        setTransactionField("counterpartyGstin", e.target.value.toUpperCase())
                      }
                    />
                  </Field>
                  <Field label="Counterparty address (optional)">
                    <Input
                      value={transactionForm.counterpartyAddress}
                      onChange={(e) => setTransactionField("counterpartyAddress", e.target.value)}
                    />
                  </Field>
                  <Field label="Counterparty pincode (optional)">
                    <Input
                      value={transactionForm.counterpartyPincode}
                      onChange={(e) => setTransactionField("counterpartyPincode", e.target.value)}
                    />
                  </Field>
                  <label className="flex items-center gap-2 self-end text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={transactionForm.isInterAccountTransfer}
                      onChange={(e) =>
                        setTransactionForm((current) => ({
                          ...current,
                          isInterAccountTransfer: e.target.checked,
                        }))
                      }
                    />
                    Inter-account transfer (reviewer-confirmed)
                  </label>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  Enter a debit or a credit, not both. A transaction with neither is rejected. The
                  inter-account flag is never inferred from the narration.
                </p>
                <Button
                  className="mt-3"
                  onClick={addTransaction}
                  disabled={!statement || createTransaction.isPending}
                >
                  <Save />
                  Add transaction
                </Button>
              </>
            )}
          </Section>
        </div>
      </div>

      <Dialog open={tallyOpen} onOpenChange={setTallyOpen}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Sync from TallyPrime</DialogTitle>
            <DialogDescription>
              Import bank accounts and voucher transactions from TallyPrime.
            </DialogDescription>
          </DialogHeader>
          <div>
            <div>
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Import bank accounts and voucher transactions from TallyPrime. Omit dates to sync
                  the current financial year. Existing transactions are never duplicated.
                </p>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Field label="Tally Company Name">
                    <Input
                      value={tallyCfg.tallyCompany}
                      onChange={(e) => setTallyCfg((c) => ({ ...c, tallyCompany: e.target.value }))}
                      placeholder={company?.legalName ?? ""}
                    />
                  </Field>
                  <Field label="From Date (optional)">
                    <Input
                      type="date"
                      value={tallyCfg.fromDate}
                      onChange={(e) => setTallyCfg((c) => ({ ...c, fromDate: e.target.value }))}
                    />
                  </Field>
                  <Field label="To Date (optional)">
                    <Input
                      type="date"
                      value={tallyCfg.toDate}
                      onChange={(e) => setTallyCfg((c) => ({ ...c, toDate: e.target.value }))}
                    />
                  </Field>
                </div>
                <Button
                  onClick={() => {
                    if (!tallyCfg.tallyCompany.trim())
                      return toast.error("Enter a Tally company name first.");
                    if (!companyId) return;
                    tallySyncMutation.mutate(
                      {
                        companyId,
                        tallyCompanyName: tallyCfg.tallyCompany,
                        fromDate: tallyCfg.fromDate || undefined,
                        toDate: tallyCfg.toDate || undefined,
                      },
                      {
                        onSuccess: (data) => {
                          if (data.success) {
                            toast.success(
                              `Tally sync complete — ${data.transactions_created} transactions, ${data.accounts_created} new accounts`,
                            );
                          } else {
                            toast.error(`Tally sync failed: ${data.error_message}`);
                          }
                        },
                        onError: (err) => toast.error(err.message),
                      },
                    );
                  }}
                  disabled={tallySyncMutation.isPending}
                >
                  <RefreshCw className={tallySyncMutation.isPending ? "animate-spin" : undefined} />
                  {tallySyncMutation.isPending ? "Syncing…" : "Sync Bank Data"}
                </Button>
                {tallySyncMutation.data &&
                  (() => {
                    const r = tallySyncMutation.data;
                    return (
                      <div className="rounded-md border bg-muted/40 p-3 text-sm space-y-2">
                        <p className={r.success ? "font-medium" : "font-medium text-destructive"}>
                          {r.success ? "Sync succeeded" : "Sync failed"}
                          {r.success && ` · ${r.from_date} to ${r.to_date}`}
                        </p>
                        {r.success ? (
                          <div className="grid grid-cols-3 gap-2 text-center text-xs">
                            <KV
                              k="Accounts"
                              v={`${r.accounts_created}c / ${r.accounts_updated}u`}
                            />
                            <KV k="Vouchers matched" v={String(r.vouchers_matched)} />
                            <KV
                              k="Transactions"
                              v={`${r.transactions_created}c / ${r.transactions_skipped}s`}
                            />
                          </div>
                        ) : (
                          <p className="text-destructive text-xs">{r.error_message}</p>
                        )}
                      </div>
                    );
                  })()}
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={accountsOpen} onOpenChange={setAccountsOpen}>
        <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Bank accounts</DialogTitle>
            <DialogDescription>
              A statement belongs to a bank account. Add accounts here before adding statements.
            </DialogDescription>
          </DialogHeader>
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
            The account number is sent once when the account is created. After that it is only ever
            shown masked.
          </p>
        </DialogContent>
      </Dialog>

      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Transaction detail</SheetTitle>
          </SheetHeader>
          {open && (
            <div className="mt-4 space-y-4">
              {editing ? (
                <div className="space-y-3">
                  <Field label="Transaction Date">
                    <Input
                      type="date"
                      value={editForm.transactionDate}
                      onChange={(e) =>
                        setEditForm((f) => ({ ...f, transactionDate: e.target.value }))
                      }
                    />
                  </Field>
                  <Field label="Value Date (optional)">
                    <Input
                      type="date"
                      value={editForm.valueDate}
                      onChange={(e) => setEditForm((f) => ({ ...f, valueDate: e.target.value }))}
                    />
                  </Field>
                  <Field label="Reference (optional)">
                    <Input
                      className="num"
                      value={editForm.referenceNumber}
                      onChange={(e) =>
                        setEditForm((f) => ({ ...f, referenceNumber: e.target.value }))
                      }
                    />
                  </Field>
                  <Field label="Narration">
                    <Input
                      value={editForm.description}
                      onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
                    />
                  </Field>
                  <Field label="Debit">
                    <Input
                      className="num"
                      inputMode="decimal"
                      placeholder="0.00"
                      value={editForm.debitAmount}
                      onChange={(e) => setEditForm((f) => ({ ...f, debitAmount: e.target.value }))}
                    />
                  </Field>
                  <Field label="Credit">
                    <Input
                      className="num"
                      inputMode="decimal"
                      placeholder="0.00"
                      value={editForm.creditAmount}
                      onChange={(e) => setEditForm((f) => ({ ...f, creditAmount: e.target.value }))}
                    />
                  </Field>
                  <Field label="Balance (optional)">
                    <Input
                      className="num"
                      inputMode="decimal"
                      placeholder="0.00"
                      value={editForm.balance}
                      onChange={(e) => setEditForm((f) => ({ ...f, balance: e.target.value }))}
                    />
                  </Field>
                  <div className="flex gap-2">
                    <Button onClick={saveEdit} disabled={updateTransaction.isPending}>
                      <Save /> Save
                    </Button>
                    <Button variant="outline" onClick={() => setEditing(false)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <>
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
                    <KV k="Counterparty" v={dash(open.counterpartyName)} />
                    <KV k="Counterparty ledger" v={dash(open.counterpartyLedger)} />
                    <KV
                      k="Inter-account transfer"
                      v={open.isInterAccountTransfer ? "Reviewer-confirmed" : "No"}
                    />
                  </div>
                  <Field label="Narration as printed by the bank">
                    <div className="num rounded border bg-muted p-2 text-xs">
                      {dash(open.description)}
                    </div>
                  </Field>
                  <Button variant="outline" size="sm" onClick={() => openEdit(open)}>
                    Edit transaction
                  </Button>
                  <p className="text-xs text-muted-foreground">
                    Stored exactly as entered. Nothing has been cleaned, categorised or
                    reinterpreted.
                  </p>
                </>
              )}
              <BankVoucherPanel
                companyId={companyId}
                statementId={statement?.id ?? ""}
                transactionId={open.id}
                transaction={open}
                onTransactionUpdated={setOpen}
              />
            </div>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
