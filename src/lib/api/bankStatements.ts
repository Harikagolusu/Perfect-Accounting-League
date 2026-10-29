/**
 * Bank Statements and Bank Transactions API contract, plus the mappers to the
 * camelCase shapes the UI renders.
 *
 * Money arrives as a decimal *string* and stays a string all the way to the
 * cell that displays it. Converting to a JavaScript number would reintroduce
 * binary floating point in the browser, which is exactly what the backend's
 * NUMERIC columns exist to avoid.
 *
 * Nothing here parses a statement. There is no extraction engine configured, so
 * the process call reports that rather than returning invented transactions.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

/** Mirrors `BankStatementStatus` in backend/app/models/bank.py. */
export type BankStatementStatus =
  "UPLOADED" | "PROCESSING" | "PROCESSED" | "NEEDS_REVIEW" | "FAILED";

/**
 * Mirrors `TransactionType`. This is the direction the bank recorded, not an
 * accounting classification: a DEBIT is not automatically an expense.
 */
export type TransactionType = "DEBIT" | "CREDIT";

export interface BankStatementDto {
  id: string;
  company_id: string;
  bank_account_id: string;
  document_id: string | null;
  statement_start_date: string | null;
  statement_end_date: string | null;
  opening_balance: string | null;
  closing_balance: string | null;
  currency: string;
  status: BankStatementStatus;
  created_at: string;
  updated_at: string;
}

export interface BankStatementWriteDto {
  bank_account_id?: string;
  document_id?: string | null;
  statement_start_date?: string | null;
  statement_end_date?: string | null;
  opening_balance?: string | null;
  closing_balance?: string | null;
  currency?: string;
  status?: BankStatementStatus;
}

export interface BankTransactionDto {
  id: string;
  company_id: string;
  bank_statement_id: string;
  transaction_date: string;
  value_date: string | null;
  description: string | null;
  reference_number: string | null;
  debit_amount: string;
  credit_amount: string;
  balance: string | null;
  transaction_type: TransactionType;
  counterparty_name: string | null;
  counterparty_ledger: string | null;
  counterparty_address: string | null;
  counterparty_pincode: string | null;
  counterparty_gstin: string | null;
  is_inter_account_transfer: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * The only shape the API accepts on write.
 *
 * `transaction_type` is absent on purpose: the backend derives it from the two
 * amount columns, so a client cannot assert a direction the money contradicts.
 */
export interface BankTransactionWriteDto {
  transaction_date: string;
  value_date?: string | null;
  description?: string | null;
  reference_number?: string | null;
  debit_amount?: string | null;
  credit_amount?: string | null;
  balance?: string | null;
  counterparty_name?: string | null;
  counterparty_ledger?: string | null;
  counterparty_address?: string | null;
  counterparty_pincode?: string | null;
  counterparty_gstin?: string | null;
  is_inter_account_transfer?: boolean;
}

export interface BankTransactionMappingWriteDto {
  counterparty_name?: string | null;
  counterparty_ledger?: string | null;
  counterparty_address?: string | null;
  counterparty_pincode?: string | null;
  counterparty_gstin?: string | null;
  is_inter_account_transfer?: boolean;
}

export interface ProcessStatementResultDto {
  statement_id: string;
  configured: boolean;
  reason: string | null;
  message: string;
  issues: string[];
  transaction_count: number | null;
}

export interface StatementFormValues {
  bankAccountId: string;
  documentId: string;
  statementStartDate: string;
  statementEndDate: string;
  openingBalance: string;
  closingBalance: string;
}

export interface TransactionFormValues {
  transactionDate: string;
  valueDate: string;
  description: string;
  referenceNumber: string;
  debitAmount: string;
  creditAmount: string;
  balance: string;
  counterpartyName: string;
  counterpartyLedger: string;
  counterpartyAddress: string;
  counterpartyPincode: string;
  counterpartyGstin: string;
  isInterAccountTransfer: boolean;
}

const blank = (v: string | null | undefined): string => v ?? "";

const optional = (v: string | undefined): string | null => {
  const trimmed = (v ?? "").trim();
  return trimmed === "" ? null : trimmed;
};

/** API payload -> UI model. Field names are converted once, here. */
export const toBankStatement = (dto: BankStatementDto) => ({
  id: dto.id,
  companyId: dto.company_id,
  bankAccountId: dto.bank_account_id,
  documentId: dto.document_id,
  statementStartDate: dto.statement_start_date,
  statementEndDate: dto.statement_end_date,
  openingBalance: dto.opening_balance,
  closingBalance: dto.closing_balance,
  currency: dto.currency,
  status: dto.status,
  createdAt: dto.created_at,
  updatedAt: dto.updated_at,
});

export type UiBankStatement = ReturnType<typeof toBankStatement>;

export const toBankTransaction = (dto: BankTransactionDto) => ({
  id: dto.id,
  companyId: dto.company_id,
  statementId: dto.bank_statement_id,
  transactionDate: dto.transaction_date,
  valueDate: dto.value_date,
  description: dto.description,
  referenceNumber: dto.reference_number,
  debitAmount: dto.debit_amount,
  creditAmount: dto.credit_amount,
  balance: dto.balance,
  transactionType: dto.transaction_type,
  counterpartyName: dto.counterparty_name,
  counterpartyLedger: dto.counterparty_ledger,
  counterpartyAddress: dto.counterparty_address,
  counterpartyPincode: dto.counterparty_pincode,
  counterpartyGstin: dto.counterparty_gstin,
  isInterAccountTransfer: dto.is_inter_account_transfer,
  createdAt: dto.created_at,
  updatedAt: dto.updated_at,
});

export type UiBankTransaction = ReturnType<typeof toBankTransaction>;

/**
 * UI edit form -> API payload.
 *
 * Balances stay decimal text all the way to the wire: no `Number()` call is
 * made anywhere on this path.
 */
export const toStatementPayload = (form: StatementFormValues): BankStatementWriteDto => ({
  bank_account_id: form.bankAccountId,
  document_id: optional(form.documentId),
  statement_start_date: optional(form.statementStartDate),
  statement_end_date: optional(form.statementEndDate),
  opening_balance: optional(form.openingBalance),
  closing_balance: optional(form.closingBalance),
});

export const toTransactionPayload = (form: TransactionFormValues): BankTransactionWriteDto => ({
  transaction_date: form.transactionDate,
  value_date: optional(form.valueDate),
  description: optional(form.description),
  reference_number: optional(form.referenceNumber),
  debit_amount: optional(form.debitAmount),
  credit_amount: optional(form.creditAmount),
  balance: optional(form.balance),
  counterparty_name: optional(form.counterpartyName),
  counterparty_ledger: optional(form.counterpartyLedger),
  counterparty_address: optional(form.counterpartyAddress),
  counterparty_pincode: optional(form.counterpartyPincode),
  counterparty_gstin: optional(form.counterpartyGstin),
  is_inter_account_transfer: form.isInterAccountTransfer,
});

export const statementToForm = (statement: UiBankStatement): StatementFormValues => ({
  bankAccountId: statement.bankAccountId,
  documentId: blank(statement.documentId),
  statementStartDate: blank(statement.statementStartDate),
  statementEndDate: blank(statement.statementEndDate),
  openingBalance: blank(statement.openingBalance),
  closingBalance: blank(statement.closingBalance),
});

export const transactionToForm = (transaction: UiBankTransaction): TransactionFormValues => ({
  transactionDate: transaction.transactionDate,
  valueDate: blank(transaction.valueDate),
  description: blank(transaction.description),
  referenceNumber: blank(transaction.referenceNumber),
  debitAmount: transaction.debitAmount,
  creditAmount: transaction.creditAmount,
  balance: blank(transaction.balance),
  counterpartyName: blank(transaction.counterpartyName),
  counterpartyLedger: blank(transaction.counterpartyLedger),
  counterpartyAddress: blank(transaction.counterpartyAddress),
  counterpartyPincode: blank(transaction.counterpartyPincode),
  counterpartyGstin: blank(transaction.counterpartyGstin),
  isInterAccountTransfer: transaction.isInterAccountTransfer,
});

/** Display labels for StatusBadge, which already understands these words. */
const UI_STATUS_LABEL: Record<BankStatementStatus, string> = {
  UPLOADED: "Uploaded",
  PROCESSING: "Processing",
  PROCESSED: "Processed",
  NEEDS_REVIEW: "Needs Review",
  FAILED: "Failed",
};

export const statusToUiLabel = (status: BankStatementStatus): string =>
  UI_STATUS_LABEL[status] ?? status;

export const STATEMENT_STATUS_OPTIONS: readonly BankStatementStatus[] = [
  "UPLOADED",
  "PROCESSING",
  "NEEDS_REVIEW",
  "PROCESSED",
  "FAILED",
];

const base = (companyId: string) => `${API_V1_PREFIX}/companies/${companyId}/bank-statements`;

export const bankStatementsApi = {
  list: (
    companyId: string,
    filters: { bankAccountId?: string; status?: BankStatementStatus } = {},
  ) =>
    apiFetch<BankStatementDto[]>(
      base(companyId),
      {},
      { bank_account_id: filters.bankAccountId, status: filters.status },
    ),
  get: (companyId: string, statementId: string) =>
    apiFetch<BankStatementDto>(`${base(companyId)}/${statementId}`),
  create: (companyId: string, body: BankStatementWriteDto) =>
    apiFetch<BankStatementDto>(base(companyId), { method: "POST", body: JSON.stringify(body) }),
  update: (companyId: string, statementId: string, body: BankStatementWriteDto) =>
    apiFetch<BankStatementDto>(`${base(companyId)}/${statementId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /**
   * Removes the statement and its transactions. The source document and the
   * bank account are left in place by the backend.
   */
  remove: (companyId: string, statementId: string) =>
    apiFetch<void>(`${base(companyId)}/${statementId}`, { method: "DELETE" }),
  /** Asks the backend to parse automatically. Today it reports it cannot. */
  process: (companyId: string, statementId: string) =>
    apiFetch<ProcessStatementResultDto>(`${base(companyId)}/${statementId}/process`, {
      method: "POST",
    }),
  listTransactions: (
    companyId: string,
    statementId: string,
    filters: { dateFrom?: string; dateTo?: string } = {},
  ) =>
    apiFetch<BankTransactionDto[]>(
      `${base(companyId)}/${statementId}/transactions`,
      {},
      { date_from: filters.dateFrom, date_to: filters.dateTo },
    ),
  getTransaction: (companyId: string, statementId: string, transactionId: string) =>
    apiFetch<BankTransactionDto>(`${base(companyId)}/${statementId}/transactions/${transactionId}`),
  createTransaction: (companyId: string, statementId: string, body: BankTransactionWriteDto) =>
    apiFetch<BankTransactionDto>(`${base(companyId)}/${statementId}/transactions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateTransaction: (
    companyId: string,
    statementId: string,
    transactionId: string,
    body: Partial<BankTransactionWriteDto>,
  ) =>
    apiFetch<BankTransactionDto>(
      `${base(companyId)}/${statementId}/transactions/${transactionId}`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
    ),
  updateTransactionMapping: (
    companyId: string,
    statementId: string,
    transactionId: string,
    body: BankTransactionMappingWriteDto,
  ) =>
    apiFetch<BankTransactionDto>(
      `${base(companyId)}/${statementId}/transactions/${transactionId}/mapping`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
    ),
};
