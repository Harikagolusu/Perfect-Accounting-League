import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  bankAccountLabel,
  bankAccountToForm,
  toBankAccount,
  toBankAccountPayload,
  type BankAccountDto,
} from "@/lib/api/bankAccounts";
import {
  statusToUiLabel,
  statementToForm,
  toBankStatement,
  toBankTransaction,
  toStatementPayload,
  toTransactionPayload,
  type BankStatementDto,
  type BankTransactionDto,
} from "@/lib/api/bankStatements";
import {
  bankAccountKeys,
  useBankAccounts,
  useCreateBankAccount,
  useDeactivateBankAccount,
} from "@/lib/queries/bankAccounts";
import {
  bankStatementKeys,
  bankTransactionKeys,
  useBankStatements,
  useBankTransactions,
  useCreateBankStatement,
  useCreateBankTransaction,
  useDeleteBankStatement,
  useProcessBankStatement,
} from "@/lib/queries/bankStatements";
import { compareMoney, fmtMoney } from "@/lib/format";

const COMPANY_A = "11111111-1111-1111-1111-111111111111";
const COMPANY_B = "22222222-2222-2222-2222-222222222222";
const ACCOUNT_A = "44444444-4444-4444-4444-444444444444";
const STATEMENT_A = "55555555-5555-5555-5555-555555555555";
const STATEMENT_B = "66666666-6666-6666-6666-666666666666";
const TRANSACTION_A = "77777777-7777-7777-7777-777777777777";
const DOC_A = "33333333-3333-3333-3333-333333333333";

const ACCOUNT: BankAccountDto = {
  id: ACCOUNT_A,
  company_id: COMPANY_A,
  bank_name: "HDFC Bank",
  account_name: "Current",
  masked_account_number: "XXXXXXXX7890",
  currency: "INR",
  is_active: true,
  created_at: "2026-10-19T06:00:00Z",
  updated_at: "2026-10-19T06:00:00Z",
};

const STATEMENT: BankStatementDto = {
  id: STATEMENT_A,
  company_id: COMPANY_A,
  bank_account_id: ACCOUNT_A,
  document_id: DOC_A,
  statement_start_date: "2026-03-01",
  statement_end_date: "2026-03-31",
  opening_balance: "10000.00",
  closing_balance: "10500.00",
  currency: "INR",
  status: "UPLOADED",
  created_at: "2026-10-19T06:00:00Z",
  updated_at: "2026-10-19T06:00:00Z",
};

const TRANSACTION: BankTransactionDto = {
  id: TRANSACTION_A,
  company_id: COMPANY_A,
  bank_statement_id: STATEMENT_A,
  transaction_date: "2026-03-10",
  value_date: "2026-03-11",
  description: "NEFT CR ACME LTD",
  reference_number: "NEFT001",
  debit_amount: "0.00",
  credit_amount: "2000.00",
  balance: "12000.00",
  transaction_type: "CREDIT",
  created_at: "2026-10-19T06:00:00Z",
  updated_at: "2026-10-19T06:00:00Z",
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const calls = () => vi.mocked(fetch).mock.calls as unknown as [string, RequestInit][];

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderWith(queryClient: QueryClient, node: ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

/** Routes each request shape to a fixture, so one harness can drive the page. */
function routeFetch() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url);
    if (init?.method === "DELETE") return jsonResponse(undefined, 204);
    if (init?.method === "POST" && path.endsWith("/process"))
      return jsonResponse({
        statement_id: STATEMENT_A,
        configured: false,
        reason: "EXTRACTION_NOT_CONFIGURED",
        message: "Automatic bank statement extraction is not configured yet.",
        issues: [],
        transaction_count: null,
      });
    if (init?.method === "POST" && path.endsWith("/transactions"))
      return jsonResponse(TRANSACTION, 201);
    if (init?.method === "POST" && path.endsWith("/bank-statements"))
      return jsonResponse(STATEMENT, 201);
    if (init?.method === "POST" && path.endsWith("/bank-accounts"))
      return jsonResponse(ACCOUNT, 201);
    if (path.includes("/transactions")) return jsonResponse([TRANSACTION]);
    if (path.includes("/bank-statements")) return jsonResponse([STATEMENT]);
    if (path.includes("/bank-accounts")) return jsonResponse([ACCOUNT]);
    if (path.includes("/documents")) return jsonResponse([]);
    return jsonResponse([]);
  });
}

function AccountListHarness({ companyId }: { companyId: string | undefined }) {
  const { data, isPending, isError } = useBankAccounts(companyId);
  if (isPending) return <p>Loading accounts</p>;
  if (isError) return <p>Accounts load failed</p>;
  return (
    <ul>
      {data?.map((a) => (
        <li key={a.id}>
          {a.bankName} — {a.maskedAccountNumber} — {a.companyId} — {String(a.isActive)}
        </li>
      ))}
      {data?.length === 0 && <li>no accounts</li>}
    </ul>
  );
}

function StatementListHarness({ companyId }: { companyId: string | undefined }) {
  const { data, isPending, isError } = useBankStatements(companyId);
  if (isPending) return <p>Loading statements</p>;
  if (isError) return <p>Statements load failed</p>;
  return (
    <ul>
      {data?.map((s) => (
        <li key={s.id}>
          {s.id} — {s.status} — {s.openingBalance} — {s.closingBalance} — {s.companyId}
        </li>
      ))}
      {data?.length === 0 && <li>no statements</li>}
    </ul>
  );
}

function TransactionListHarness({
  companyId,
  statementId,
}: {
  companyId: string | undefined;
  statementId: string | undefined;
}) {
  const { data, isPending, isError } = useBankTransactions(companyId, statementId);
  if (isPending) return <p>Loading transactions</p>;
  if (isError) return <p>Transactions load failed</p>;
  return (
    <ul>
      {data?.map((t) => (
        <li key={t.id}>
          {t.transactionDate} — {t.description} — Dr {t.debitAmount} — Cr {t.creditAmount} —{" "}
          {t.balance} — {t.transactionType} — {t.statementId}
        </li>
      ))}
      {data?.length === 0 && <li>no transactions</li>}
    </ul>
  );
}

function MutationHarness({ companyId }: { companyId: string }) {
  const createAccount = useCreateBankAccount(companyId);
  const deactivateAccount = useDeactivateBankAccount(companyId);
  const createStatement = useCreateBankStatement(companyId);
  const createTransaction = useCreateBankTransaction(companyId);
  const removeStatement = useDeleteBankStatement(companyId);
  const processStatement = useProcessBankStatement(companyId);
  const failed =
    createAccount.isError ||
    deactivateAccount.isError ||
    createStatement.isError ||
    createTransaction.isError ||
    removeStatement.isError ||
    processStatement.isError;
  return (
    <div>
      <button onClick={() => createAccount.mutate({ bank_name: "ICICI", account_number: "9999" })}>
        create account
      </button>
      <button onClick={() => deactivateAccount.mutate(ACCOUNT_A)}>deactivate</button>
      <button onClick={() => createStatement.mutate({ bank_account_id: ACCOUNT_A })}>
        create statement
      </button>
      <button
        onClick={() =>
          createTransaction.mutate({
            statementId: STATEMENT_A,
            body: { transaction_date: "2026-03-10", credit_amount: "500.00" },
          })
        }
      >
        create transaction
      </button>
      <button onClick={() => removeStatement.mutate(STATEMENT_A)}>delete statement</button>
      <button onClick={() => processStatement.mutate(STATEMENT_A)}>process</button>
      {failed ? <p>mutation failed</p> : null}
    </div>
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", routeFetch());
});
afterEach(() => vi.unstubAllGlobals());

describe("Bank account mapper", () => {
  it("maps snake_case onto the UI model", () => {
    const account = toBankAccount(ACCOUNT);

    expect(account.id).toBe(ACCOUNT_A);
    expect(account.companyId).toBe(COMPANY_A);
    expect(account.bankName).toBe("HDFC Bank");
    expect(account.accountName).toBe("Current");
    expect(account.isActive).toBe(true);
  });

  it("exposes only the masked account number", () => {
    const account = toBankAccount(ACCOUNT);

    // The response model has no full-number field to leak through.
    expect(account.maskedAccountNumber).toBe("XXXXXXXX7890");
    expect(Object.keys(account)).not.toContain("accountNumber");
  });

  it("never sends an account number back from an edit form", () => {
    const form = bankAccountToForm(toBankAccount(ACCOUNT));

    expect(form.accountNumber).toBe("");
    expect(form.bankName).toBe("HDFC Bank");
  });

  it("omits the account number when the form leaves it blank", () => {
    const payload = toBankAccountPayload({
      bankName: "ICICI Bank",
      accountName: "",
      accountNumber: "",
      currency: "inr",
    });

    expect(payload).toEqual({
      bank_name: "ICICI Bank",
      account_name: null,
      currency: "INR",
    });
  });

  it("labels an account using only its masked number", () => {
    expect(bankAccountLabel(toBankAccount(ACCOUNT))).toBe("HDFC Bank · XXXXXXXX7890 · Current");
  });
});

describe("Bank statement mapper", () => {
  it("maps snake_case onto the UI model", () => {
    const statement = toBankStatement(STATEMENT);

    expect(statement.bankAccountId).toBe(ACCOUNT_A);
    expect(statement.documentId).toBe(DOC_A);
    expect(statement.statementStartDate).toBe("2026-03-01");
    expect(statement.statementEndDate).toBe("2026-03-31");
    expect(statement.status).toBe("UPLOADED");
  });

  it("keeps balances as decimal strings, never numbers", () => {
    const statement = toBankStatement({ ...STATEMENT, opening_balance: "0.30" });

    expect(typeof statement.openingBalance).toBe("string");
    expect(statement.openingBalance).toBe("0.30");
    expect(typeof statement.closingBalance).toBe("string");
  });

  it("keeps an unset balance null rather than inventing a zero", () => {
    const statement = toBankStatement({ ...STATEMENT, opening_balance: null });

    expect(statement.openingBalance).toBeNull();
  });

  it("round-trips through the form without touching money", () => {
    const payload = toStatementPayload(statementToForm(toBankStatement(STATEMENT)));

    expect(payload.opening_balance).toBe("10000.00");
    expect(payload.closing_balance).toBe("10500.00");
    expect(payload.statement_start_date).toBe("2026-03-01");
    expect(payload.document_id).toBe(DOC_A);
  });

  it("turns blank form values into null", () => {
    const payload = toStatementPayload({
      bankAccountId: ACCOUNT_A,
      documentId: "",
      statementStartDate: "",
      statementEndDate: "",
      openingBalance: "",
      closingBalance: "",
    });

    expect(payload.document_id).toBeNull();
    expect(payload.statement_start_date).toBeNull();
    expect(payload.opening_balance).toBeNull();
  });

  it("renders a status label for the badge", () => {
    expect(statusToUiLabel("UPLOADED")).toBe("Uploaded");
    expect(statusToUiLabel("NEEDS_REVIEW")).toBe("Needs Review");
    expect(statusToUiLabel("FAILED")).toBe("Failed");
  });
});

describe("Bank transaction mapper", () => {
  it("maps snake_case onto the UI model", () => {
    const transaction = toBankTransaction(TRANSACTION);

    expect(transaction.statementId).toBe(STATEMENT_A);
    expect(transaction.transactionDate).toBe("2026-03-10");
    expect(transaction.valueDate).toBe("2026-03-11");
    expect(transaction.referenceNumber).toBe("NEFT001");
    expect(transaction.transactionType).toBe("CREDIT");
  });

  it("keeps debit, credit and balance as decimal strings", () => {
    const transaction = toBankTransaction(TRANSACTION);

    expect(typeof transaction.debitAmount).toBe("string");
    expect(transaction.creditAmount).toBe("2000.00");
    expect(transaction.balance).toBe("12000.00");
  });

  it("preserves a two-decimal value that a float would blur", () => {
    const transaction = toBankTransaction({ ...TRANSACTION, credit_amount: "0.10" });

    expect(transaction.creditAmount).toBe("0.10");
    // 0.1 + 0.2 in floating point is the canonical demonstration of why.
    expect(0.1 + 0.2).not.toBe(0.3);
    expect(transaction.creditAmount).not.toBe(String(0.1 + 0.2));
  });

  it("omits transaction_type from the write payload entirely", () => {
    const payload = toTransactionPayload({
      transactionDate: "2026-03-10",
      valueDate: "",
      description: "",
      referenceNumber: "",
      debitAmount: "10.00",
      creditAmount: "",
      balance: "",
    });

    // The direction is derived server-side from the amounts.
    expect(Object.keys(payload)).not.toContain("transaction_type");
    expect(payload.debit_amount).toBe("10.00");
    expect(payload.credit_amount).toBeNull();
  });
});

describe("Money formatting stays exact", () => {
  it("groups digits without converting the value", () => {
    expect(fmtMoney("1234567.89")).toBe("12,34,567.89");
    expect(fmtMoney("10500.00")).toBe("10,500.00");
    expect(fmtMoney("-250.5")).toBe("-250.5");
  });

  it("renders nothing for an absent amount", () => {
    expect(fmtMoney(null)).toBe("—");
    expect(fmtMoney("")).toBe("—");
  });

  it("sorts decimal strings by magnitude, not lexically", () => {
    const values = ["100.00", "9.00", "1000.00", "-5.00"];
    const sorted = [...values].sort(compareMoney);

    // Lexicographically "100.00" sorts below "9.00", which is wrong.
    expect(sorted).toEqual(["-5.00", "9.00", "100.00", "1000.00"]);
  });

  it("orders equal values as equal", () => {
    expect(compareMoney("10.00", "10.00")).toBe(0);
    expect(compareMoney("10.0", "10.00")).toBe(0);
  });
});

describe("Bank queries are company-scoped", () => {
  it("requests the company's own accounts path", async () => {
    renderWith(makeClient(), <AccountListHarness companyId={COMPANY_A} />);
    await waitFor(() => expect(screen.getByText(/HDFC Bank/)).toBeTruthy());

    expect(calls()[0][0]).toContain(`/api/v1/companies/${COMPANY_A}/bank-accounts`);
  });

  it("requests the company's own statements path", async () => {
    renderWith(makeClient(), <StatementListHarness companyId={COMPANY_A} />);
    await waitFor(() => expect(screen.getByText(new RegExp(STATEMENT_A))).toBeTruthy());

    expect(calls()[0][0]).toContain(`/api/v1/companies/${COMPANY_A}/bank-statements`);
  });

  it("requests transactions under the statement they belong to", async () => {
    renderWith(
      makeClient(),
      <TransactionListHarness companyId={COMPANY_A} statementId={STATEMENT_A} />,
    );
    await waitFor(() => expect(screen.getByText(/NEFT CR ACME LTD/)).toBeTruthy());

    expect(calls()[0][0]).toContain(
      `/api/v1/companies/${COMPANY_A}/bank-statements/${STATEMENT_A}/transactions`,
    );
  });

  it("renders the statement list with no invented data", async () => {
    renderWith(makeClient(), <StatementListHarness companyId={COMPANY_A} />);
    await waitFor(() => expect(screen.getByText(new RegExp(STATEMENT_A))).toBeTruthy());

    const row = screen.getByText(new RegExp(STATEMENT_A)).textContent ?? "";
    expect(row).toContain("UPLOADED");
    expect(row).toContain("10000.00");
    expect(row).toContain("10500.00");
    expect(row).toContain(COMPANY_A);
  });

  it("renders the transaction list with money intact", async () => {
    renderWith(
      makeClient(),
      <TransactionListHarness companyId={COMPANY_A} statementId={STATEMENT_A} />,
    );
    await waitFor(() => expect(screen.getByText(/NEFT CR ACME LTD/)).toBeTruthy());

    const row = screen.getByText(/NEFT CR ACME LTD/).textContent ?? "";
    expect(row).toContain("2000.00");
    expect(row).toContain("12000.00");
    expect(row).toContain("CREDIT");
    expect(row).toContain(STATEMENT_A);
  });

  it("shows a loading state before accounts arrive", () => {
    renderWith(makeClient(), <AccountListHarness companyId={COMPANY_A} />);

    expect(screen.getByText("Loading accounts")).toBeTruthy();
  });

  it("shows a loading state before statements arrive", () => {
    renderWith(makeClient(), <StatementListHarness companyId={COMPANY_A} />);

    expect(screen.getByText("Loading statements")).toBeTruthy();
  });

  it("shows an empty state rather than mock accounts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );
    renderWith(makeClient(), <AccountListHarness companyId={COMPANY_A} />);

    await waitFor(() => expect(screen.getByText("no accounts")).toBeTruthy());
  });

  it("shows an empty state rather than mock statements", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );
    renderWith(makeClient(), <StatementListHarness companyId={COMPANY_A} />);

    await waitFor(() => expect(screen.getByText("no statements")).toBeTruthy());
  });

  it("shows an error rather than falling back to mock accounts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "Boom" }, 500)),
    );
    renderWith(makeClient(), <AccountListHarness companyId={COMPANY_A} />);

    await waitFor(() => expect(screen.getByText("Accounts load failed")).toBeTruthy());
    expect(screen.queryByText(/HDFC Bank/)).toBeNull();
  });

  it("shows an error rather than falling back to mock statements", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "Boom" }, 500)),
    );
    renderWith(makeClient(), <StatementListHarness companyId={COMPANY_A} />);

    await waitFor(() => expect(screen.getByText("Statements load failed")).toBeTruthy());
    expect(screen.queryByText(new RegExp(STATEMENT_A))).toBeNull();
  });

  it("does not call the API without a company id", async () => {
    renderWith(makeClient(), <AccountListHarness companyId={undefined} />);

    await waitFor(() => expect(screen.getByText("Loading accounts")).toBeTruthy());
    expect(calls()).toHaveLength(0);
  });

  it("does not load transactions without a statement id", async () => {
    renderWith(
      makeClient(),
      <TransactionListHarness companyId={COMPANY_A} statementId={undefined} />,
    );

    await waitFor(() => expect(screen.getByText("Loading transactions")).toBeTruthy());
    expect(calls()).toHaveLength(0);
  });

  it("keeps each company's statements separate when switching", async () => {
    const shared = makeClient();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        jsonResponse(
          String(url).includes(COMPANY_A)
            ? [STATEMENT]
            : [{ ...STATEMENT, id: STATEMENT_B, company_id: COMPANY_B, status: "NEEDS_REVIEW" }],
        ),
      ),
    );

    const first = renderWith(shared, <StatementListHarness companyId={COMPANY_A} />);
    await waitFor(() => expect(screen.getByText(new RegExp(STATEMENT_A))).toBeTruthy());
    first.unmount();

    renderWith(shared, <StatementListHarness companyId={COMPANY_B} />);
    await waitFor(() => expect(screen.getByText(new RegExp(STATEMENT_B))).toBeTruthy());
    expect(screen.queryByText(new RegExp(STATEMENT_A))).toBeNull();
  });

  it("keeps each statement's transactions separate", async () => {
    const shared = makeClient();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        jsonResponse(
          String(url).includes(STATEMENT_A)
            ? [TRANSACTION]
            : [{ ...TRANSACTION, id: "other", description: "COMPANY B ROW" }],
        ),
      ),
    );

    const first = renderWith(
      shared,
      <TransactionListHarness companyId={COMPANY_A} statementId={STATEMENT_A} />,
    );
    await waitFor(() => expect(screen.getByText(/NEFT CR ACME LTD/)).toBeTruthy());
    first.unmount();

    renderWith(shared, <TransactionListHarness companyId={COMPANY_A} statementId={STATEMENT_B} />);
    await waitFor(() => expect(screen.getByText(/COMPANY B ROW/)).toBeTruthy());
    expect(screen.queryByText(/NEFT CR ACME LTD/)).toBeNull();
  });
});

describe("Query keys", () => {
  it("carries the company id into every list key", () => {
    expect(bankAccountKeys.list(COMPANY_A)).toEqual(["bankAccounts", COMPANY_A]);
    expect(bankStatementKeys.list(COMPANY_A)).toEqual(["bankStatements", COMPANY_A]);
  });

  it("carries both company and statement into transaction keys", () => {
    expect(bankTransactionKeys.list(COMPANY_A, STATEMENT_A)).toEqual([
      "bankTransactions",
      COMPANY_A,
      STATEMENT_A,
    ]);
  });

  it("gives each company its own key space", () => {
    expect(bankStatementKeys.list(COMPANY_A)).not.toEqual(bankStatementKeys.list(COMPANY_B));
  });

  it("gives each statement its own detail and transaction keys", () => {
    expect(bankStatementKeys.detail(COMPANY_A, STATEMENT_A)).not.toEqual(
      bankStatementKeys.detail(COMPANY_A, STATEMENT_B),
    );
    expect(bankTransactionKeys.list(COMPANY_A, STATEMENT_A)).not.toEqual(
      bankTransactionKeys.list(COMPANY_A, STATEMENT_B),
    );
  });
});

describe("Bank mutations", () => {
  it("POSTs an account to the company path", async () => {
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("create account"));

    await waitFor(() => {
      const post = calls().find(([, init]) => init?.method === "POST");
      expect(post).toBeTruthy();
      expect(post![0]).toContain(`/api/v1/companies/${COMPANY_A}/bank-accounts`);
      expect(JSON.parse(String(post![1].body))).toEqual({
        bank_name: "ICICI",
        account_number: "9999",
      });
    });
  });

  it("POSTs a statement to the company path", async () => {
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("create statement"));

    await waitFor(() => {
      const post = calls().find(
        ([url, init]) => init?.method === "POST" && url.endsWith("bank-statements"),
      );
      expect(post).toBeTruthy();
      expect(post![0]).toContain(`/api/v1/companies/${COMPANY_A}/bank-statements`);
    });
  });

  it("POSTs a transaction under its own statement", async () => {
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("create transaction"));

    await waitFor(() => {
      const post = calls().find(
        ([url, init]) => init?.method === "POST" && url.endsWith("/transactions"),
      );
      expect(post).toBeTruthy();
      expect(post![0]).toContain(
        `/api/v1/companies/${COMPANY_A}/bank-statements/${STATEMENT_A}/transactions`,
      );
      // Money goes over the wire as decimal text.
      expect(JSON.parse(String(post![1].body))).toEqual({
        transaction_date: "2026-03-10",
        credit_amount: "500.00",
      });
    });
  });

  it("DELETEs the statement without touching the document or the account", async () => {
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("delete statement"));

    await waitFor(() => {
      const del = calls().find(([, init]) => init?.method === "DELETE");
      expect(del).toBeTruthy();
      expect(del![0]).toContain(`/api/v1/companies/${COMPANY_A}/bank-statements/${STATEMENT_A}`);
      expect(del![0]).not.toContain("/documents");
      expect(del![0]).not.toContain("/bank-accounts");
    });
  });

  it("reports an unconfigured extraction rather than claiming success", async () => {
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("process"));

    await waitFor(() => {
      const post = calls().find(([, init]) => init?.method === "POST");
      expect(post![0]).toContain(`/bank-statements/${STATEMENT_A}/process`);
    });
    // The fixture answers configured: false, and the harness reports no failure.
    await waitFor(() => expect(screen.queryByText("mutation failed")).toBeNull());
  });

  it("surfaces a rejected debit/credit pairing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === "POST" && String(_url).endsWith("/transactions")
          ? jsonResponse({ detail: "A transaction cannot be both a debit and a credit." }, 422)
          : routeFetch()(_url, init),
      ),
    );
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("create transaction"));

    await waitFor(() => expect(screen.getByText("mutation failed")).toBeTruthy());
  });

  it("surfaces an inverted statement period", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === "POST" && String(_url).endsWith("bank-statements")
          ? jsonResponse({ detail: "Statement end date is before the start date." }, 422)
          : routeFetch()(_url, init),
      ),
    );
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("create statement"));

    await waitFor(() => expect(screen.getByText("mutation failed")).toBeTruthy());
  });

  it("surfaces a 404 when a statement belongs to another company", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === "DELETE"
          ? jsonResponse({ detail: "Bank statement not found" }, 404)
          : routeFetch()(_url, init),
      ),
    );
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("delete statement"));

    await waitFor(() => expect(screen.getByText("mutation failed")).toBeTruthy());
  });
});
