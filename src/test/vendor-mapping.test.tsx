import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VendorMappingPanel } from "@/components/pal/vendor-mapping";
import type { Vendor } from "@/lib/types";

const COMPANY_A = "11111111-1111-1111-1111-111111111111";

const VENDOR: Vendor = {
  id: "vendor-a",
  companyId: COMPANY_A,
  name: "ABC Traders",
  gstin: "27AABCT1234F1Z5",
  pan: "AABCT1234F",
  defaultExpenseLedger: "Purchase - Raw Material",
  creditorLedger: "ABC Traders",
  parentGroup: "Raw Material",
  transactions: 0,
  lastUsed: "",
  status: "Active",
};

function noop() {}

function renderPanel(overrides: Partial<Parameters<typeof VendorMappingPanel>[0]> = {}) {
  return render(
    <VendorMappingPanel
      status="UNMAPPED"
      matchMethod={null}
      vendorId={null}
      suggestedVendorId={null}
      vendors={[VENDOR]}
      onConfirm={noop}
      onReject={noop}
      onMap={noop}
      {...overrides}
    />,
  );
}

describe("Vendor mapping panel", () => {
  it("shows a matched vendor, its evidence and historical grouping", () => {
    renderPanel({
      status: "MATCHED",
      matchMethod: "EXACT_GSTIN",
      vendorId: VENDOR.id,
    });

    expect(screen.getAllByText("ABC Traders").length).toBeGreaterThan(0);
    expect(screen.getByText("Matched by GSTIN")).toBeTruthy();
    expect(screen.getByText("Matched")).toBeTruthy();
    expect(screen.getByText("Raw Material")).toBeTruthy();
    expect(screen.getByText("Purchase - Raw Material")).toBeTruthy();
    expect(
      screen.queryByText("No vendor match found. Choose a vendor to map this invoice."),
    ).toBeNull();
  });

  it("shows the immediately previous vendor after an explicit re-map", () => {
    renderPanel({
      status: "CONFIRMED",
      matchMethod: null,
      vendorId: VENDOR.id,
      previousVendorId: "vendor-old",
      vendors: [
        VENDOR,
        { ...VENDOR, id: "vendor-old", name: "Previous Vendor", status: "Inactive" },
      ],
    });

    expect(screen.getByText(/Previously mapped to Previous Vendor/)).toBeTruthy();
  });

  it("shows the no-match state and allows a manual company-scoped vendor choice", () => {
    const onMap = vi.fn();
    renderPanel({ onMap });

    expect(screen.getByText(/No vendor match found/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Map to vendor"), {
      target: { value: VENDOR.id },
    });
    fireEvent.click(screen.getByText("Map vendor"));

    expect(onMap).toHaveBeenCalledWith(VENDOR.id);
  });

  it("shows an ambiguous state and its real candidate vendors", () => {
    renderPanel({
      status: "AMBIGUOUS",
      candidates: [
        {
          id: VENDOR.id,
          name: VENDOR.name,
          gstin: VENDOR.gstin,
          pan: VENDOR.pan,
          parentGroup: VENDOR.parentGroup,
          defaultExpenseLedger: VENDOR.defaultExpenseLedger,
          creditorLedger: VENDOR.creditorLedger,
        },
        {
          id: "vendor-b",
          name: "ABC Traders Pvt Ltd",
          gstin: "27AABCT1234F1Z6",
          pan: "AABCT1234G",
          parentGroup: "Raw Material",
          defaultExpenseLedger: "Purchase - Raw Material",
          creditorLedger: "ABC Traders Pvt Ltd",
        },
      ],
    });

    expect(screen.getByText(/Multiple possible vendors or conflicting identifiers/)).toBeTruthy();
    expect(screen.getByText(/Manual review required/)).toBeTruthy();
    expect(screen.getByText(/ABC Traders Pvt Ltd/)).toBeTruthy();
    expect(screen.queryByText(/confidence/i)).toBeNull();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("shows a name suggestion and confirms it only on user action", () => {
    const onConfirm = vi.fn();
    renderPanel({
      status: "SUGGESTED",
      matchMethod: "NORMALIZED_NAME",
      suggestedVendorId: VENDOR.id,
      onConfirm,
    });

    expect(screen.getByText("Suggestion: ABC Traders")).toBeTruthy();
    expect(screen.getByText(/not applied until the mapping is confirmed/)).toBeTruthy();
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Confirm"));

    expect(onConfirm).toHaveBeenCalledWith(VENDOR.id);
  });

  it("labels a vendor created automatically from the invoice data", () => {
    renderPanel({
      status: "SUGGESTED",
      matchMethod: null,
      suggestedVendorId: VENDOR.id,
    });

    expect(screen.getByText(/New vendor created from this invoice/)).toBeTruthy();
    expect(
      screen.getByText(/vendor record was created from the extracted supplier details/),
    ).toBeTruthy();
    expect(screen.getByText(/not applied until the mapping is confirmed/)).toBeTruthy();
  });

  it("can reject a suggestion without assigning a vendor", () => {
    const onReject = vi.fn();
    const onConfirm = vi.fn();
    renderPanel({
      status: "SUGGESTED",
      matchMethod: "NORMALIZED_NAME",
      suggestedVendorId: VENDOR.id,
      onConfirm,
      onReject,
    });

    fireEvent.click(screen.getByText("Reject"));

    expect(onReject).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("shows a rejected state and keeps the manual mapping controls available", () => {
    renderPanel({ status: "REJECTED" });

    expect(screen.getByText(/Suggestion rejected/)).toBeTruthy();
    expect(screen.getByLabelText("Map to vendor")).toBeTruthy();
  });

  it("surfaces an error from the mapping request", () => {
    renderPanel({ error: "Vendor not found" });

    expect(screen.getByText("Vendor not found")).toBeTruthy();
  });

  it("explains that processing creates a vendor when none exist", () => {
    renderPanel({ vendors: [] });

    expect(
      screen.getByText(/Processing this invoice creates one from its supplier details/),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /create vendor/i })).toBeNull();
    expect(screen.getByLabelText("Map to vendor")).toBeDisabled();
  });

  it("does not offer inactive vendors for new mappings", () => {
    renderPanel({
      vendors: [{ ...VENDOR, id: "inactive", name: "Inactive Vendor", status: "Inactive" }],
    });

    expect(screen.queryByText("Inactive Vendor")).toBeNull();
    expect(
      screen.getByText(/Processing this invoice creates one from its supplier details/),
    ).toBeTruthy();
  });
});
