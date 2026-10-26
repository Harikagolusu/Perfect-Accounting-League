import { describe, expect, it } from "vitest";

import {
  stateFromGstin,
  statusToApi,
  statusToUi,
  toCompany,
  toCompanyPayload,
  type CompanyDto,
} from "@/lib/api/companies";

const DTO: CompanyDto = {
  id: "1f0c6d2a-2c3b-4a5d-9e8f-0a1b2c3d4e5f",
  name: "Sahyadri Agro",
  legal_name: "Sahyadri Agro Foods Private Limited",
  gstin: "27AAJCS4821K1Z5",
  pan: "AAJCS4821K",
  email: "accounts@sahyadri-demo.in",
  phone: "+91 98220 11223",
  address: "Plot 14, MIDC Bhosari",
  cgst_ledger: null,
  sgst_ledger: null,
  igst_ledger: null,
  cess_ledger: null,
  round_off_ledger: null,
  sales_ledger: null,
  financial_year: "FY 2026-27",
  status: "ACTIVE",
  created_at: "2026-10-19T06:00:00Z",
  updated_at: "2026-10-19T06:00:00Z",
};

describe("Company API mapping", () => {
  it("maps snake_case payloads onto the UI model", () => {
    const company = toCompany(DTO);

    expect(company.id).toBe(DTO.id);
    expect(company.displayName).toBe("Sahyadri Agro");
    expect(company.legalName).toBe("Sahyadri Agro Foods Private Limited");
    expect(company.financialYear).toBe("FY 2026-27");
  });

  it("replaces mock ids with the backend uuid", () => {
    expect(toCompany(DTO).id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("maps every backend status onto a UI status", () => {
    expect(statusToUi("ACTIVE")).toBe("Active");
    expect(statusToUi("INACTIVE")).toBe("Inactive");
    expect(statusToUi("ONBOARDING")).toBe("Onboarding");
  });

  it("maps UI statuses back onto the API enum", () => {
    expect(statusToApi("Active")).toBe("ACTIVE");
    expect(statusToApi("Inactive")).toBe("INACTIVE");
    expect(statusToApi("Onboarding")).toBe("ONBOARDING");
  });

  it("derives the state from the GSTIN instead of storing it", () => {
    expect(stateFromGstin("27AAJCS4821K1Z5")).toBe("Maharashtra");
    expect(stateFromGstin("29AARFN7310M1Z2")).toBe("Karnataka");
    expect(stateFromGstin("")).toBe("");
    expect(stateFromGstin(null)).toBe("");
  });

  it("fills fields the database does not own with honest defaults", () => {
    const company = toCompany(DTO);

    expect(company.state).toBe("Maharashtra");
    expect(company.software).toBe("None");
    expect(company.assignedUsers).toEqual([]);
    expect(company.contactPerson).toBe("");
  });

  it("coerces null columns to empty strings for the UI", () => {
    const company = toCompany({
      ...DTO,
      legal_name: null,
      address: null,
      phone: null,
      financial_year: null,
    });

    expect(company.legalName).toBe("");
    expect(company.address).toBe("");
    expect(company.phone).toBe("");
    expect(company.financialYear).toBe("");
  });

  it("sends only stored fields, converting blanks to null", () => {
    const payload = toCompanyPayload(toCompany({ ...DTO, phone: null, address: "  " }));

    expect(Object.keys(payload).sort()).toEqual(
      [
        "address",
        "email",
        "financial_year",
        "gstin",
        "legal_name",
        "name",
        "pan",
        "phone",
        "status",
      ].sort(),
    );
    expect(payload.phone).toBeNull();
    expect(payload.address).toBeNull();
    expect(payload.name).toBe("Sahyadri Agro");
    expect(payload.status).toBe("ACTIVE");
  });

  it("round-trips a company without losing stored data", () => {
    const company = toCompany(DTO);

    expect(toCompanyPayload(company)).toEqual({
      name: "Sahyadri Agro",
      legal_name: "Sahyadri Agro Foods Private Limited",
      gstin: "27AAJCS4821K1Z5",
      pan: "AAJCS4821K",
      email: "accounts@sahyadri-demo.in",
      phone: "+91 98220 11223",
      address: "Plot 14, MIDC Bhosari",
      financial_year: "FY 2026-27",
      status: "ACTIVE",
    });
  });
});
