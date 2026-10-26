/**
 * Ledger Master inside Tally Integration.
 *
 * Ledgers are company-owned. The form creates records through the backend; it
 * never seeds a chart of accounts or invents a parent group.
 */

import { useState } from "react";
import { Plus, Power, PowerOff } from "lucide-react";
import { toast } from "sonner";

import { EmptyState, Field, Section, StatusBadge } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useCompany } from "@/lib/store";
import { useUpdateCompanyAccounting } from "@/lib/queries/companies";
import {
  useCreateLedger,
  useDeactivateLedger,
  useLedgers,
  useUpdateLedger,
} from "@/lib/queries/ledgers";

export function LedgerMaster({ companyId }: { companyId: string }) {
  const { data: ledgers = [], isPending, isError, error } = useLedgers(companyId, true);
  const createLedger = useCreateLedger(companyId);
  const updateLedger = useUpdateLedger(companyId);
  const deactivateLedger = useDeactivateLedger(companyId);
  const [name, setName] = useState("");
  const [ledgerType, setLedgerType] = useState("");
  const [parentGroup, setParentGroup] = useState("");

  const add = async () => {
    if (!name.trim()) {
      toast.error("Ledger name is required.");
      return;
    }
    try {
      await createLedger.mutateAsync({
        name: name.trim(),
        ledgerType: ledgerType.trim(),
        parentGroup: parentGroup.trim(),
      });
      setName("");
      setLedgerType("");
      setParentGroup("");
      toast.success("Ledger created");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not create the ledger");
    }
  };

  const deactivate = async (id: string) => {
    try {
      await deactivateLedger.mutateAsync(id);
      toast.success("Ledger deactivated");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not deactivate the ledger");
    }
  };

  const reactivate = async (id: string) => {
    try {
      await updateLedger.mutateAsync({ id, changes: { isActive: true } });
      toast.success("Ledger reactivated");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not reactivate the ledger");
    }
  };

  return (
    <Section title="Ledger Master" className="lg:col-span-3">
      <p className="mb-3 text-xs text-muted-foreground">
        Company-scoped ledger names used by voucher suggestions and exports. Nothing is seeded; add
        the company's own chart of accounts. Existing vouchers keep their name snapshot when a
        ledger is deactivated.
      </p>
      <div className="grid gap-2 md:grid-cols-4">
        <Field label="Ledger name">
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Freight Inward"
          />
        </Field>
        <Field label="Type (optional)">
          <Input
            value={ledgerType}
            onChange={(event) => setLedgerType(event.target.value)}
            placeholder="Company's own type"
          />
        </Field>
        <Field label="Parent group (optional)">
          <Input
            value={parentGroup}
            onChange={(event) => setParentGroup(event.target.value)}
            placeholder="e.g. Indirect Expenses"
          />
        </Field>
        <div className="flex items-end">
          <Button onClick={add} disabled={createLedger.isPending} className="w-full">
            <Plus /> Add ledger
          </Button>
        </div>
      </div>

      <div className="mt-4 overflow-x-auto rounded border">
        {isPending ? (
          <p className="p-4 text-sm text-muted-foreground">Loading ledgers...</p>
        ) : isError ? (
          <p className="p-4 text-sm text-destructive">
            {error instanceof Error ? error.message : "Could not load ledgers"}
          </p>
        ) : ledgers.length === 0 ? (
          <EmptyState
            title="No ledgers yet"
            hint="Add the company's ledger names before generating a voucher."
          />
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th className="p-2">Name</th>
                <th className="p-2">Type</th>
                <th className="p-2">Parent group</th>
                <th className="p-2">Status</th>
                <th className="w-24 p-2" />
              </tr>
            </thead>
            <tbody>
              {ledgers.map((ledger) => (
                <tr key={ledger.id} className="border-t">
                  <td className="p-2 font-medium">{ledger.name}</td>
                  <td className="p-2 text-muted-foreground">{ledger.ledgerType || "-"}</td>
                  <td className="p-2 text-muted-foreground">{ledger.parentGroup || "-"}</td>
                  <td className="p-2">
                    <StatusBadge status={ledger.isActive ? "Active" : "Inactive"} />
                  </td>
                  <td className="p-2 text-right">
                    {ledger.isActive ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => deactivate(ledger.id)}
                        disabled={deactivateLedger.isPending}
                      >
                        <PowerOff /> Deactivate
                      </Button>
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => reactivate(ledger.id)}
                        disabled={updateLedger.isPending}
                      >
                        <Power /> Reactivate
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Section>
  );
}

/**
 * Company-level ledger defaults. These names are configuration, not accounting
 * data: the voucher factory resolves them against the Ledger Master and routes
 * a missing name to review instead of creating it.
 */
export function CompanyAccountingDefaults({ companyId }: { companyId: string }) {
  const company = useCompany();
  const update = useUpdateCompanyAccounting(companyId);
  const [values, setValues] = useState({
    cgst: company?.cgstLedger ?? "",
    sgst: company?.sgstLedger ?? "",
    igst: company?.igstLedger ?? "",
    cess: company?.cessLedger ?? "",
    roundOff: company?.roundOffLedger ?? "",
    sales: company?.salesLedger ?? "",
  });

  const set = (key: keyof typeof values, value: string) =>
    setValues((current) => ({ ...current, [key]: value }));

  const save = async () => {
    try {
      await update.mutateAsync({
        cgst_ledger: values.cgst.trim() || null,
        sgst_ledger: values.sgst.trim() || null,
        igst_ledger: values.igst.trim() || null,
        cess_ledger: values.cess.trim() || null,
        round_off_ledger: values.roundOff.trim() || null,
        sales_ledger: values.sales.trim() || null,
      });
      toast.success("Company ledger defaults saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save ledger defaults");
    }
  };

  return (
    <Section title="Company Ledger Defaults" className="lg:col-span-3">
      <p className="mb-3 text-xs text-muted-foreground">
        The deterministic voucher factory uses these names for tax, round-off and sales lines. They
        must already exist in the Ledger Master; a missing name becomes a review exception rather
        than a guessed ledger.
      </p>
      <div className="grid gap-2 md:grid-cols-3">
        {(
          [
            ["CGST ledger", "cgst"],
            ["SGST ledger", "sgst"],
            ["IGST ledger", "igst"],
            ["Cess ledger", "cess"],
            ["Round-off ledger", "roundOff"],
            ["Sales ledger", "sales"],
          ] as [string, keyof typeof values][]
        ).map(([label, key]) => (
          <Field key={key} label={label}>
            <Input value={values[key]} onChange={(event) => set(key, event.target.value)} />
          </Field>
        ))}
      </div>
      <Button className="mt-3" onClick={save} disabled={update.isPending}>
        Save ledger defaults
      </Button>
    </Section>
  );
}
