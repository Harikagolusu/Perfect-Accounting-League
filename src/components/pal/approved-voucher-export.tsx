/**
 * Approved-voucher import export inside Tally Integration.
 *
 * Generating the client workbook moves approved vouchers to
 * `AWAITING_IMPORT_CONFIRMATION`; it never marks them posted. Posting is
 * verified later from the voucher review surface.
 */

import { Download } from "lucide-react";
import { toast } from "sonner";

import { EmptyState, Section, StatusBadge } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { downloadBlob } from "@/lib/export";
import { fmtDate } from "@/lib/format";
import { useExportVouchers, useVoucherExportPreview, useVouchers } from "@/lib/queries/vouchers";
import { voucherStatusLabel, voucherTypeLabel } from "@/lib/api/vouchers";

const exportable = new Set(["APPROVED", "AWAITING_IMPORT_CONFIRMATION"]);

export function ApprovedVoucherExport({ companyId }: { companyId: string }) {
  const { data: vouchers = [], isPending } = useVouchers(companyId);
  const { data: preview } = useVoucherExportPreview(companyId);
  const exportVouchers = useExportVouchers(companyId);

  const selected = vouchers.filter((voucher) => exportable.has(voucher.status));

  const run = async () => {
    if (!selected.length) {
      toast.error("No approved vouchers are ready to export.");
      return;
    }
    try {
      const file = await exportVouchers.mutateAsync({
        voucherIds: selected.map((voucher) => voucher.id),
      });
      downloadBlob(file.filename, file.blob);
      toast.success("Import file generated", {
        description:
          "Vouchers are awaiting import confirmation. Mark them posted only after checking the imported entries.",
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not generate the import file");
    }
  };

  return (
    <Section title="Approved Voucher Export" className="lg:col-span-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs text-muted-foreground">
            The client-format workbook contains only approved vouchers. Generating it is an export,
            not a posting: the status becomes Awaiting import confirmation and the posted date
            remains empty until a verified external reference is recorded.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {preview
              ? `${preview.vouchers} vouchers, ${preview.rows} rows. ${
                  preview.exceptions.length
                    ? `${preview.exceptions.length} exception(s) excluded.`
                    : "No blocking exceptions."
                }`
              : "Preparing preview..."}
          </p>
        </div>
        <Button onClick={run} disabled={exportVouchers.isPending || !selected.length}>
          <Download /> Generate accounting workbook
        </Button>
      </div>

      <div className="mt-3 overflow-x-auto rounded border">
        {isPending ? (
          <p className="p-4 text-sm text-muted-foreground">Loading vouchers...</p>
        ) : selected.length === 0 ? (
          <EmptyState
            title="No approved vouchers"
            hint="Approve a draft voucher in Invoice Processing or Bank Statements first."
          />
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th className="p-2">Voucher</th>
                <th className="p-2">Date</th>
                <th className="p-2">Type</th>
                <th className="p-2">Status</th>
                <th className="p-2 text-right">Debit</th>
                <th className="p-2 text-right">Credit</th>
              </tr>
            </thead>
            <tbody>
              {selected.map((voucher) => (
                <tr key={voucher.id} className="border-t">
                  <td className="num p-2 font-medium">{voucher.voucherNumber}</td>
                  <td className="p-2">{fmtDate(voucher.voucherDate)}</td>
                  <td className="p-2">{voucherTypeLabel(voucher.voucherType)}</td>
                  <td className="p-2">
                    <StatusBadge status={voucherStatusLabel(voucher.status)} />
                  </td>
                  <td className="num p-2 text-right">{voucher.totalDebit}</td>
                  <td className="num p-2 text-right">{voucher.totalCredit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {preview && preview.exceptions.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-destructive">
          {preview.exceptions.slice(0, 5).map((issue, index) => (
            <li key={`${issue.code}-${index}`}>
              {issue.code}: {issue.message}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
