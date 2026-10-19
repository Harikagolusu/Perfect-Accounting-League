/**
 * Bank Accounts API contract and the mapper to the camelCase shape the UI uses.
 *
 * The account number is write-only from this layer's point of view. A request
 * may carry the real number, but the response only ever holds
 * `masked_account_number`, so the full number cannot be rendered, logged or
 * copied into a table by accident.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

export interface BankAccountDto {
  id: string;
  company_id: string;
  bank_name: string;
  account_name: string | null;
  /** Already masked by the backend, e.g. "XXXXXXXX7890". */
  masked_account_number: string;
  currency: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** The only shape the API accepts on write; unknown keys are rejected. */
export interface BankAccountWriteDto {
  bank_name?: string;
  account_name?: string | null;
  account_number?: string;
  currency?: string;
  is_active?: boolean;
}

export interface BankAccountFormValues {
  bankName: string;
  accountName: string;
  accountNumber: string;
  currency: string;
}

const blank = (v: string | null | undefined): string => v ?? "";

/** API payload -> UI model. Field names are converted once, here. */
export const toBankAccount = (dto: BankAccountDto) => ({
  id: dto.id,
  companyId: dto.company_id,
  bankName: dto.bank_name,
  accountName: dto.account_name,
  maskedAccountNumber: dto.masked_account_number,
  currency: dto.currency,
  isActive: dto.is_active,
  createdAt: dto.created_at,
  updatedAt: dto.updated_at,
});

export type UiBankAccount = ReturnType<typeof toBankAccount>;

/** UI edit form -> API payload. Empty optional text becomes null. */
export const toBankAccountPayload = (form: BankAccountFormValues): BankAccountWriteDto => {
  const accountName = form.accountName.trim();
  const accountNumber = form.accountNumber.trim();
  return {
    bank_name: form.bankName.trim(),
    account_name: accountName === "" ? null : accountName,
    // Only sent when the user typed one; an account keeps its existing number.
    ...(accountNumber === "" ? {} : { account_number: accountNumber }),
    currency: form.currency.trim().toUpperCase() || "INR",
  };
};

export const bankAccountToForm = (account: UiBankAccount): BankAccountFormValues => ({
  bankName: account.bankName,
  accountName: blank(account.accountName),
  // The masked form is shown, never the real number: it is not returned.
  accountNumber: "",
  currency: account.currency,
});

/** Label for the account dropdown. The number is already masked. */
export const bankAccountLabel = (account: UiBankAccount): string =>
  `${account.bankName} · ${account.maskedAccountNumber}${
    account.accountName ? ` · ${account.accountName}` : ""
  }`;

const base = (companyId: string) => `${API_V1_PREFIX}/companies/${companyId}/bank-accounts`;

export const bankAccountsApi = {
  list: (companyId: string) => apiFetch<BankAccountDto[]>(base(companyId)),
  get: (companyId: string, accountId: string) =>
    apiFetch<BankAccountDto>(`${base(companyId)}/${accountId}`),
  create: (companyId: string, body: BankAccountWriteDto) =>
    apiFetch<BankAccountDto>(base(companyId), { method: "POST", body: JSON.stringify(body) }),
  update: (companyId: string, accountId: string, body: BankAccountWriteDto) =>
    apiFetch<BankAccountDto>(`${base(companyId)}/${accountId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /** Deactivates rather than deletes: statements still reference the account. */
  deactivate: (companyId: string, accountId: string) =>
    apiFetch<BankAccountDto>(`${base(companyId)}/${accountId}`, { method: "DELETE" }),
};
