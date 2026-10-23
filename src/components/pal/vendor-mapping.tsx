import { useState } from "react";
import { AlertTriangle, Check, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  mappingStatusLabel,
  mappingSummaryLabel,
  type VendorMappingStatus,
  type VendorMatchCandidate,
  type VendorMatchMethod,
} from "@/lib/api/invoices";
import type { Vendor } from "@/lib/types";

const selectCls = "h-9 rounded-md border border-input bg-background px-2 text-sm";

interface VendorMappingPanelProps {
  status: VendorMappingStatus;
  matchMethod: VendorMatchMethod | null;
  vendorId: string | null;
  suggestedVendorId: string | null;
  previousVendorId?: string | null;
  vendors: Vendor[];
  candidates?: VendorMatchCandidate[];
  onConfirm: (vendorId: string) => void;
  onReject: () => void;
  onMap: (vendorId: string) => void;
  pending?: boolean;
  error?: string | null;
}

function Grouping({ vendor, pending }: { vendor: Vendor; pending?: boolean }) {
  return (
    <div className="mt-3 grid gap-2 text-sm md:grid-cols-3">
      <div>
        <div className="text-xs text-muted-foreground">Parent Group</div>
        <div>{vendor.parentGroup || "Not configured"}</div>
      </div>
      <div>
        <div className="text-xs text-muted-foreground">Default Expense Ledger</div>
        <div>{vendor.defaultExpenseLedger || "Not configured"}</div>
      </div>
      <div>
        <div className="text-xs text-muted-foreground">Sundry Creditor Ledger</div>
        <div>{vendor.creditorLedger || "Not configured"}</div>
      </div>
      {pending && (
        <p className="text-xs text-muted-foreground md:col-span-3">
          Vendor grouping is not applied until the mapping is confirmed.
        </p>
      )}
    </div>
  );
}

/**
 * Minimal vendor-mapping panel for the Invoice page.
 *
 * The backend can create a vendor master record from an unmatched invoice's
 * printed supplier details, but always links it as a suggestion. This panel
 * shows that suggestion and requires a person to confirm it; no confidence
 * percentage is invented.
 */
export function VendorMappingPanel({
  status,
  matchMethod,
  vendorId,
  suggestedVendorId,
  previousVendorId = null,
  vendors,
  candidates = [],
  onConfirm,
  onReject,
  onMap,
  pending = false,
  error = null,
}: VendorMappingPanelProps) {
  const [choice, setChoice] = useState("");

  const assigned = vendors.find((vendor) => vendor.id === vendorId);
  const suggested = vendors.find((vendor) => vendor.id === suggestedVendorId);
  const previous = vendors.find((vendor) => vendor.id === previousVendorId);
  const selectable = vendors.filter((vendor) => vendor.status !== "Inactive");
  const assignedMissing = Boolean(vendorId && !assigned);

  const mapSelected = () => {
    if (choice) onMap(choice);
  };

  return (
    <div className="text-sm">
      {assigned ? (
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{assigned.name}</span>
            <span className="rounded border px-2 py-0.5 text-xs text-muted-foreground">
              {mappingStatusLabel(status)}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {mappingSummaryLabel(status, matchMethod)}
          </p>
          {previous && previous.id !== assigned.id && (
            <p className="mt-1 text-xs text-muted-foreground">
              Previously mapped to {previous.name}.
            </p>
          )}
          <Grouping vendor={assigned} />
        </div>
      ) : assignedMissing ? (
        <p className="text-sm text-muted-foreground">
          The assigned vendor record is no longer available. Choose a current vendor below.
        </p>
      ) : suggested ? (
        <div className="rounded-md border bg-muted/40 p-3">
          <p className="font-medium">Suggestion: {suggested.name}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {mappingSummaryLabel(status, matchMethod)}. Confirm before it is used for accounting.
          </p>
          {matchMethod === null && (
            <p className="mt-1 text-xs text-muted-foreground">
              The vendor record was created from the extracted supplier details. Set its expense
              ledger in the Vendor Master after confirming the identity.
            </p>
          )}
          <Grouping vendor={suggested} pending />
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() => onConfirm(suggested.id)}
              disabled={pending}
              type="button"
            >
              <Check />
              Confirm
            </Button>
            <Button size="sm" variant="outline" onClick={onReject} disabled={pending} type="button">
              <X />
              Reject
            </Button>
          </div>
        </div>
      ) : status === "AMBIGUOUS" ? (
        <div>
          <p className="flex items-start gap-2 text-sm">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
            Multiple possible vendors or conflicting identifiers. Manual review required.
          </p>
          {candidates.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
              {candidates.map((candidate) => (
                <li key={candidate.id}>
                  {candidate.name}
                  {candidate.gstin
                    ? ` · ${candidate.gstin}`
                    : candidate.pan
                      ? ` · ${candidate.pan}`
                      : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : status === "REJECTED" ? (
        <p className="text-sm text-muted-foreground">
          Suggestion rejected. Choose a vendor below, or leave this invoice unmapped.
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">
          No vendor match found. When supplier details are available, processing creates a vendor
          automatically from the extracted name and GSTIN/PAN.
        </p>
      )}

      {!assigned && (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Map to vendor
            <select
              aria-label="Map to vendor"
              className={selectCls}
              value={choice}
              onChange={(event) => setChoice(event.target.value)}
              disabled={pending || !selectable.length}
            >
              <option value="">Choose a vendor…</option>
              {selectable.map((vendor) => (
                <option key={vendor.id} value={vendor.id}>
                  {vendor.name}
                </option>
              ))}
            </select>
          </label>
          <Button
            size="sm"
            variant="outline"
            onClick={mapSelected}
            disabled={!choice || pending}
            type="button"
          >
            Map vendor
          </Button>
        </div>
      )}

      {!selectable.length && (
        <p className="mt-2 text-xs text-muted-foreground">
          No vendors yet. Processing this invoice creates one from its supplier details.
        </p>
      )}

      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}
