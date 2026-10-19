import { Download } from "lucide-react";
import { DataTable } from "./data-table";
import { StatusBadge } from "./common";
import { Button } from "@/components/ui/button";
import { useCompany, useScoped, useStore } from "@/lib/store";
import { fmtDate } from "@/lib/format";
import { exportFile } from "@/lib/export";
import type { ExportJob } from "@/lib/types";
import { toast } from "sonner";

/** Re-generates a summary CSV for a historical export job (actual file is held by the backend). */
export function redownload(job: ExportJob) {
  if (job.status !== "Generated") { toast.error("This export failed and has no file."); return; }
  exportFile("CSV", job.id, [{ ExportID: job.id, Name: job.name, Type: job.type, Records: job.records, Generated: job.generatedAt, Target: job.target }]);
  toast.success(`Downloaded manifest for ${job.id}`);
}

export function ExportHistory({ target }: { target?: ExportJob["target"] }) {
  const s = useStore();
  const company = useCompany();
  const rows = useScoped(s.exports).filter((e) => !target || e.target === target);
  return (
    <DataTable
      rows={rows}
      rowKey={(r) => r.id}
      pageSize={6}
      columns={[
        { key: "id", header: "Export ID", cell: (r) => <span className="num">{r.id}</span> },
        { key: "company", header: "Company", cell: () => company?.displayName ?? "" },
        { key: "records", header: "Records", align: "right", sortValue: (r) => r.records },
        { key: "type", header: "Export Type", cell: (r) => `${r.type} (${r.format})` },
        { key: "generatedAt", header: "Generated", cell: (r) => fmtDate(r.generatedAt) },
        { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
        { key: "d", header: "", cell: (r) => <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Download" disabled={r.status !== "Generated"} onClick={() => redownload(r)}><Download /></Button> },
      ]}
    />
  );
}
