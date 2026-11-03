import { createFileRoute } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Check, Download, Loader2, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useCompany } from "@/lib/store";
import { useTallyBankDataSync, useTallyMappingSync } from "@/lib/queries/tally";
import { fmtDate, fmtMoney, compareMoney } from "@/lib/format";
import { downloadBlob, exportFile } from "@/lib/export";
import { BankVoucherPanel } from "@/components/pal/bank-voucher";
import { LedgerCombobox } from "@/components/pal/voucher-review";
import { useLedgers } from "@/lib/queries/ledgers";
import { cn } from "@/lib/utils";
import { exportsApi, firstExportError } from "@/lib/api/exports";
import { vouchersApi } from "@/lib/api/vouchers";
import { toBankAccountPayload, type BankAccountFormValues } from "@/lib/api/bankAccounts";
import {
  statusToUiLabel,
  toTransactionPayload,
  type TransactionFormValues,
  type UiBankTransaction,
} from "@/lib/api/bankStatements";
import {
  useBankAccounts,
  useCreateBankAccount,
  useDeactivateBankAccount,
} from "@/lib/queries/bankAccounts";
import {
  useBankStatementLedgerSuggestions,
  useBankStatements,
  useBankTransactions,
  useCreateBankTransaction,
  useDeleteBankStatement,
  useIngestBankStatementsBatch,
  useUpdateBankTransaction,
  useUpdateBankTransactionMapping,
} from "@/lib/queries/bankStatements";
import { nextBatchCode } from "@/lib/api/documents";
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

const emptyAccountForm: BankAccountFormValues = {
  bankName: "",
  accountName: "",
  accountNumber: "",
  currency: "INR",
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

/** One extraction queue is capped so the run stays responsive and reviewable. */
const MAX_BATCH = 10;

function BankPage() {
  const company = useCompany();
  const companyId = company?.id;

  const accountsQuery = useBankAccounts(companyId);
  const statementsQuery = useBankStatements(companyId);
  const documentsQuery = useDocuments(companyId);
  const uploadDocuments = useUploadDocuments(companyId);
  const createAccount = useCreateBankAccount(companyId);
  const deactivateAccount = useDeactivateBankAccount(companyId);
  const createTransaction = useCreateBankTransaction(companyId);
  const deleteStatement = useDeleteBankStatement(companyId);
  const ingestBatch = useIngestBankStatementsBatch(companyId);
  const updateTransaction = useUpdateBankTransaction(companyId);
  const updateMapping = useUpdateBankTransactionMapping(companyId);

  const tallySyncMutation = useTallyBankDataSync();
  const mappingSyncMutation = useTallyMappingSync();
  const [tallyCfg, setTallyCfg] = useState({
    tallyCompany: company?.legalName ?? "",
    fromDate: "",
    toDate: "",
  });

  const accounts = useMemo(() => accountsQuery.data ?? [], [accountsQuery.data]);
  const statements = useMemo(() => statementsQuery.data ?? [], [statementsQuery.data]);
  const documents = useMemo(() => documentsQuery.data ?? [], [documentsQuery.data]);

  const [accountForm, setAccountForm] = useState<BankAccountFormValues>(emptyAccountForm);
  const [transactionForm, setTransactionForm] =
    useState<TransactionFormValues>(emptyTransactionForm);
  const [staged, setStaged] = useState<File[]>([]);
  // Session-only cards for the live extraction queue, mirroring Document Inbox.
  const [batchItems, setBatchItems] = useState<
    {
      id: string;
      name: string;
      status: "queued" | "extracting" | "completed" | "review" | "failed";
      detail?: string;
    }[]
  >([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [statementId, setStatementId] = useState("");
  const [batch, setBatch] = useState("All");
  const [type, setType] = useState("All");
  const [dFrom, setDFrom] = useState("");
  const [dTo, setDTo] = useState("");
  const [open, setOpen] = useState<UiBankTransaction | null>(null);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<TransactionFormValues>(emptyTransactionForm);
  const [showAddForm, setShowAddForm] = useState(false);
  const [exportingClient, setExportingClient] = useState(false);
  const [postingTally, setPostingTally] = useState(false);
  // Transactions posted to Tally this session; their checkbox is disabled so
  // they are not posted twice (fromBank is not idempotent — see shortcut below).
  const [postedIds, setPostedIds] = useState<Set<string>>(new Set());
  // Id of the single row posting from the Tally column, for its spinner.
  const [postingId, setPostingId] = useState<string | null>(null);
  const acceptLedger = async (txnId: string, name: string) => {
    if (!statement || !name.trim()) return;
    try {
      await updateMapping.mutateAsync({
        statementId: statement.id,
        transactionId: txnId,
        body: { counterparty_ledger: name.trim() },
      });
      toast.success("Ledger mapped");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not map the ledger");
    }
  };
  const queryClient = useQueryClient();
  const [tallyOpen, setTallyOpen] = useState(false);
  const [accountsOpen, setAccountsOpen] = useState(false);

  // Follow the company: a switch must not leave another company's selection.
  useEffect(() => {
    setStatementId("");
    setStaged([]);
    setBatchItems([]);
    setProgress(null);
    setPostedIds(new Set());
    setTallyCfg((c) => ({ ...c, tallyCompany: company?.legalName ?? "" }));
  }, [companyId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Pre-fill Tally company name from the loaded company record.
  useEffect(() => {
    if (company?.legalName)
      setTallyCfg((c) => (c.tallyCompany ? c : { ...c, tallyCompany: company.legalName }));
  }, [company?.legalName]);

  // A statement's batch lives on its source document (same as invoices), so the
  // selector can be narrowed to one upload batch.
  const batchByDoc = useMemo(() => new Map(documents.map((d) => [d.id, d.batchCode])), [documents]);
  // Only batches that actually back a bank statement — invoice-upload batches
  // live in the same documents table but must not appear here.
  const batchCodes = useMemo(() => {
    const stmtDocIds = new Set(statements.map((st) => st.documentId).filter(Boolean));
    return Array.from(
      new Set(
        documents
          .filter((d) => stmtDocIds.has(d.id))
          .map((d) => d.batchCode)
          .filter(Boolean),
      ),
    ) as string[];
  }, [statements, documents]);
  const visibleStatements = useMemo(
    () =>
      batch === "All"
        ? statements
        : statements.filter(
            (st) => ((st.documentId ? batchByDoc.get(st.documentId) : null) ?? null) === batch,
          ),
    [statements, batch, batchByDoc],
  );

  // Keep a valid selection as statements (and the batch filter) come and go.
  useEffect(() => {
    if (visibleStatements.length === 0) {
      setStatementId("");
      return;
    }
    if (!visibleStatements.some((st) => st.id === statementId))
      setStatementId(visibleStatements[0].id);
  }, [visibleStatements, statementId]);

  const statement = statements.find((st) => st.id === statementId) ?? null;

  const transactionsQuery = useBankTransactions(companyId, statement?.id, {
    dateFrom: dFrom || undefined,
    dateTo: dTo || undefined,
  });
  const ledgerSuggestionsQuery = useBankStatementLedgerSuggestions(companyId, statement?.id);
  const ledgerSuggestions = ledgerSuggestionsQuery.data ?? {};
  const ledgersQuery = useLedgers(companyId);
  const activeLedgers = useMemo(
    () => (ledgersQuery.data ?? []).filter((l) => l.isActive),
    [ledgersQuery.data],
  );
  const transactions = useMemo(() => {
    const rows = transactionsQuery.data ?? [];
    return type === "All" ? rows : rows.filter((t) => t.transactionType === type.toUpperCase());
  }, [transactionsQuery.data, type]);

  const loading = accountsQuery.isPending || statementsQuery.isPending;
  const failed = accountsQuery.isError || statementsQuery.isError || transactionsQuery.isError;

  const setAccountField = (k: keyof BankAccountFormValues, v: string) =>
    setAccountForm((f) => ({ ...f, [k]: v }));
  const setTransactionField = (k: keyof TransactionFormValues, v: string) =>
    setTransactionForm((f) => ({ ...f, [k]: v }));

  const saveAccount = async () => {
    if (!accountForm.bankName.trim()) return toast.error("Bank name is required.");
    if (!accountForm.accountNumber.trim()) return toast.error("Account number is required.");
    try {
      const created = await createAccount.mutateAsync(toBankAccountPayload(accountForm));
      setAccountForm(emptyAccountForm);
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

  // One file finishing -> its card status. A statement created and cleanly
  // processed is a tick; anything else (needs review, failed read, no matching
  // account) needs a human.
  const ingestCard = (r: { statementId: string | null; status: string | null }) =>
    r.statementId && r.status === "PROCESSED"
      ? ("completed" as const)
      : r.statementId && r.status === "NEEDS_REVIEW"
        ? ("review" as const)
        : ("failed" as const);

  const uploadStatements = async () => {
    if (!staged.length) return toast.error("Choose at least one statement file.");
    if (staged.length > MAX_BATCH) {
      return toast.error(`Maximum ${MAX_BATCH} statements can be extracted at a time.`);
    }
    const files = staged;
    setStaged([]);
    // One shared code for this whole upload, so statements can be filtered to it.
    const batchCode = nextBatchCode(
      documents.map((d) => d.batchCode),
      "Bank",
    );
    // Each file goes to the Document Inbox; ingest then reads it, finds which
    // bank account it belongs to, and creates the statement. No account picked.
    const docs: { id: string; name: string }[] = [];
    try {
      for (const f of files) {
        const [uploaded] = await uploadDocuments.mutateAsync({ files: [f], batchCode });
        docs.push({ id: uploaded.id, name: f.name });
      }
    } catch (e) {
      setStaged((prev) => [...files, ...prev]);
      toast.error(e instanceof Error ? e.message : "Could not upload files");
      return;
    }
    toast.success(`${docs.length} file${docs.length === 1 ? "" : "s"} uploaded — extracting…`);
    setBatchItems(docs.map((d) => ({ id: d.id, name: d.name, status: "queued" as const })));
    setProgress({ done: 0, total: docs.length });
    try {
      const results = await ingestBatch.mutateAsync({
        documentIds: docs.map((d) => d.id),
        onProgress: (p) => {
          setProgress({ done: p.done, total: p.total });
          setBatchItems((prev) =>
            prev.map((it, idx) => {
              if (p.last && it.id === p.last.documentId) {
                const status = ingestCard(p.last);
                return {
                  ...it,
                  status,
                  detail: status === "completed" ? undefined : p.last.message,
                };
              }
              if (idx === p.done && p.done < p.total) return { ...it, status: "extracting" };
              return it;
            }),
          );
        },
      });
      const firstCreated = results.find((r) => r.statementId);
      if (firstCreated?.statementId) setStatementId(firstCreated.statementId);
      const needAttention = results.filter(
        (r) => !r.statementId || r.status !== "PROCESSED",
      ).length;
      if (results.every((r) => !r.configured)) {
        toast.info("Automatic extraction is not configured.");
      } else if (needAttention) {
        toast.warning(`${needAttention} of ${results.length} file(s) need attention.`);
      } else {
        toast.success("All statements extracted.");
      }
    } catch (e) {
      setProgress(null);
      toast.error(e instanceof Error ? e.message : "Extraction failed");
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

  // Post selected transactions to TallyPrime: each becomes a voucher, then
  // submit -> approve -> post-to-tally. Sequential so a mid-run Tally failure
  // stops cleanly and the summary is accurate.
  // shortcut: fromBank is not idempotent, so re-posting the same transaction
  // creates a second draft voucher; add a transaction->voucher link to guard.
  const exportSelectedToTally = async (ids: string[], clear: () => void) => {
    if (!companyId || ids.length === 0) return;
    // A ledger must be confirmed manually first; an unconfirmed suggestion
    // (counterpartyLedger still null) is not a mapping and must not post.
    const byId = new Map(transactions.map((t) => [t.id, t]));
    const mapped = ids.filter((id) => byId.get(id)?.counterpartyLedger);
    if (mapped.length < ids.length) {
      toast.warning(
        `${ids.length - mapped.length} transaction(s) have no ledger mapped — map them manually before posting to Tally.`,
      );
    }
    if (mapped.length === 0) return;
    ids = mapped;
    setPostingTally(true);
    let posted = 0;
    const failures: string[] = [];
    const done: string[] = [];
    for (const id of ids) {
      try {
        const voucher = await vouchersApi.fromBank(companyId, id);
        await vouchersApi.submit(companyId, voucher.id);
        await vouchersApi.approve(companyId, voucher.id);
        const res = await vouchersApi.postToTally(companyId, voucher.id);
        if (res.success || res.already_posted) {
          posted += 1;
          done.push(id);
        } else failures.push(res.message || voucher.voucher_number);
      } catch (e) {
        failures.push(e instanceof Error ? e.message : String(e));
      }
    }
    if (done.length) setPostedIds((prev) => new Set([...prev, ...done]));
    queryClient.invalidateQueries({ queryKey: ["vouchers"] });
    queryClient.invalidateQueries({ queryKey: ["bankStatements"] });
    setPostingTally(false);
    clear();
    if (posted) toast.success(`Posted ${posted} voucher(s) to Tally`);
    if (failures.length) toast.error(`${failures.length} failed: ${failures[0]}`);
  };

  // Post a single transaction from the Tally column; reuses the batch path.
  const postOneToTally = async (id: string) => {
    setPostingId(id);
    await exportSelectedToTally([id], () => {});
    setPostingId(null);
  };

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
      key: "description",
      header: "Raw Narration",
      cell: (r) =>
        r.description ? (
          <span className="num block min-w-[13rem] whitespace-normal break-words text-xs">
            {r.description}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      key: "ledger",
      header: "Ledger",
      sortValue: (r) => r.counterpartyLedger ?? ledgerSuggestions[r.id] ?? "",
      cell: (r) => {
        const confirmed = r.counterpartyLedger;
        const suggested = ledgerSuggestions[r.id] ?? "";
        // Confirmed reads plainly; an AI/history suggestion reads as italic muted
        // text until confirmed. Only a row with no name at all gets the picker.
        if (confirmed || suggested) {
          return (
            <span
              className={cn(
                "block min-w-[11rem] whitespace-normal break-words",
                !confirmed && "italic text-muted-foreground",
              )}
              title={
                confirmed ? confirmed : `Suggested from Tally history / narration: ${suggested}`
              }
            >
              {confirmed || suggested}
            </span>
          );
        }
        return (
          <div className="min-w-[13rem]" onClick={(e) => e.stopPropagation()}>
            <LedgerCombobox
              ledgers={activeLedgers}
              value={null}
              onChange={(id) => {
                const name = activeLedgers.find((l) => l.id === id)?.name;
                if (name) void acceptLedger(r.id, name);
              }}
            />
          </div>
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
    {
      key: "tally",
      header: "Tally",
      align: "right",
      cell: (r) => {
        if (postedIds.has(r.id)) {
          return (
            <span
              title="Posted to Tally"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-input text-success"
            >
              <Check className="h-4 w-4" />
            </span>
          );
        }
        return (
          <button
            onClick={(e) => {
              e.stopPropagation();
              void postOneToTally(r.id);
            }}
            disabled={postingTally}
            aria-label="Post to Tally"
            title="Post this transaction to TallyPrime"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-input transition-colors hover:bg-muted disabled:opacity-70"
          >
            {postingId === r.id ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <img src="/tally.png" alt="" className="h-4 w-4" />
            )}
          </button>
        );
      },
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
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">
              Drop your bank statements — PAL reads each one, works out which bank account it
              belongs to, and extracts the transactions. No need to pick an account.
            </p>
            <FileUploadDropzone
              multiple
              accept=".pdf,.xlsx,.xls,.csv"
              hint="PDF, Excel or CSV statements — up to 10 at once"
              onFiles={(f) => setStaged((p) => [...p, ...f])}
            />
            {staged.length > 0 && (
              <ul className="divide-y rounded border text-sm">
                {staged.map((f, i) => (
                  <li
                    key={`${f.name}-${i}`}
                    className="flex items-center justify-between px-3 py-1.5"
                  >
                    <span className="truncate">{f.name}</span>
                    <button
                      className="text-xs text-destructive hover:underline"
                      onClick={() => setStaged((p) => p.filter((_, j) => j !== i))}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {staged.length > MAX_BATCH && (
              <p className="text-xs text-destructive">
                Maximum {MAX_BATCH} statements can be extracted at a time.
              </p>
            )}
            <div>
              <Button
                onClick={uploadStatements}
                disabled={
                  !staged.length ||
                  staged.length > MAX_BATCH ||
                  uploadDocuments.isPending ||
                  ingestBatch.isPending
                }
              >
                {uploadDocuments.isPending
                  ? "Uploading…"
                  : ingestBatch.isPending
                    ? "Extracting…"
                    : staged.length > 1
                      ? `Upload & extract ${staged.length}`
                      : "Upload & extract"}
              </Button>
            </div>
          </div>
        </Section>

        {batchItems.length > 0 && (
          <div className="rounded-md border bg-card p-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">Extracting bank statements</h3>
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
                <li key={it.id} className="px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
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
                      {it.status === "queued" && (
                        <span className="text-muted-foreground">Queued</span>
                      )}
                    </span>
                  </div>
                  {it.detail && (it.status === "failed" || it.status === "review") && (
                    <p className="mt-0.5 text-xs text-muted-foreground">{it.detail}</p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

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
            ) : (
              <>
                {batchCodes.length > 0 && (
                  <select
                    aria-label="Filter by batch code"
                    className={selectCls + " mb-3"}
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
                )}
                {statement ? (
                  <>
                    <div className="flex flex-wrap items-center gap-2">
                      <select
                        aria-label="Select statement"
                        className={selectCls + " min-w-64 flex-1"}
                        value={statementId}
                        onChange={(e) => setStatementId(e.target.value)}
                      >
                        {visibleStatements.map((st) => {
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
                ) : (
                  <p className="text-sm text-muted-foreground">No statements in this batch.</p>
                )}
              </>
            )}
          </Section>
        </div>

        {/* Transactions (full width) */}
        <div className="flex flex-col gap-4">
          <div>
            <DataTable
              wrap
              rows={transactions}
              rowKey={(r) => r.id}
              pageSize={20}
              selectable
              rowSelectable={(r) => !postedIds.has(r.id)}
              bulkActions={(ids, clear) => (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7"
                  disabled={postingTally}
                  onClick={() => exportSelectedToTally(ids, clear)}
                >
                  {postingTally ? <Loader2 className="animate-spin" /> : <Download />}
                  {postingTally ? "Posting…" : "Export to Tally"}
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
                            // Learn ledger mappings from Tally's old vouchers so
                            // the table can suggest a ledger per transaction.
                            mappingSyncMutation.mutate(
                              { companyId, tallyCompanyName: tallyCfg.tallyCompany },
                              {
                                onSuccess: () =>
                                  queryClient.invalidateQueries({
                                    queryKey: ["bankTransactions"],
                                  }),
                              },
                            );
                          } else {
                            toast.error(`Tally sync failed: ${data.error_message}`);
                          }
                        },
                        onError: (err) => toast.error(err.message),
                      },
                    );
                  }}
                  disabled={tallySyncMutation.isPending || mappingSyncMutation.isPending}
                >
                  <RefreshCw
                    className={
                      tallySyncMutation.isPending || mappingSyncMutation.isPending
                        ? "animate-spin"
                        : undefined
                    }
                  />
                  {tallySyncMutation.isPending
                    ? "Syncing…"
                    : mappingSyncMutation.isPending
                      ? "Learning mappings…"
                      : "Sync Bank Data"}
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

      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent className="max-h-[90vh] w-[95vw] max-w-6xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Transaction detail</DialogTitle>
          </DialogHeader>
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
              />
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
