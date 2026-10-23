import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import {
  FileStack,
  CheckCircle2,
  Clock,
  XCircle,
  Users,
  AlertTriangle,
  Upload,
  Landmark,
  GitCompareArrows,
  Calculator,
  Percent,
  ArrowRight,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Legend,
} from "recharts";
import { EmptyState, KPICard, PageHeader, Section, StatusBadge } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FileUploadDropzone } from "@/components/pal/dropzone";
import { useCompany, useScoped, useStore } from "@/lib/store";
import { useDocuments, useUploadDocuments } from "@/lib/queries/documents";
import { useVendors } from "@/lib/queries/vendors";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/")({
  head: () =>
    seo(
      "Accounting Operations",
      "Monitor document processing, accounting workflows and tax reconciliation.",
    ),
  component: Dashboard,
});

function Dashboard() {
  const s = useStore();
  const company = useCompany();
  const navigate = useNavigate();
  const { data: docs = [], refetch: refetchDocuments } = useDocuments(company?.id);
  const { data: vendors = [] } = useVendors(company?.id, true);
  const uploadDocuments = useUploadDocuments(company?.id);
  const reco = useScoped(s.reco);
  const reviews = useScoped(s.reviews);
  const audit = useScoped(s.audit);
  const [upload, setUpload] = useState(false);

  const activity = useMemo(() => {
    const days = Array.from({ length: 14 }, (_, index) => {
      const date = new Date();
      date.setDate(date.getDate() - (13 - index));
      return date.toISOString().slice(0, 10);
    });
    return days.map((date) => {
      const dayDocs = docs.filter((d) => d.receivedAt.slice(0, 10) === date);
      return {
        date: date.slice(5),
        processed: dayDocs.filter((d) => ["Extracted", "Approved", "Exported"].includes(d.status))
          .length,
        pending: dayDocs.filter((d) =>
          ["Uploaded", "Received", "Queued", "Processing", "Needs Review"].includes(d.status),
        ).length,
        failed: dayDocs.filter((d) => d.status === "Failed").length,
      };
    });
  }, [docs]);

  if (!company) {
    return (
      <>
        <PageHeader
          title="Accounting Operations"
          description="Monitor document processing, accounting workflows, and tax reconciliation."
        />
        <div className="rounded-md border bg-card p-4">
          <EmptyState
            title="No company selected"
            hint="Create a company to start uploading documents and adding vendors."
          />
          <div className="flex justify-center">
            <Button onClick={() => navigate({ to: "/companies" })}>Add a company</Button>
          </div>
        </div>
      </>
    );
  }

  const processed = docs.filter((d) =>
    ["Extracted", "Approved", "Exported"].includes(d.status),
  ).length;
  const pending = docs.filter((d) =>
    ["Uploaded", "Received", "Queued", "Processing", "Needs Review"].includes(d.status),
  ).length;
  const failed = docs.filter((d) => d.status === "Failed").length;
  const exceptions = reco.filter((r) => r.status !== "Exact Match").length;
  const count = (st: string) => reco.filter((r) => r.status === st).length;

  const addFiles = async (files: File[]) => {
    try {
      const saved = await uploadDocuments.mutateAsync(files);
      await refetchDocuments();
      toast.success(
        `${saved.length} file${saved.length === 1 ? "" : "s"} uploaded to ${company.displayName}`,
      );
      setUpload(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Upload failed");
    }
  };

  return (
    <>
      <PageHeader
        title="Accounting Operations"
        description="Monitor document processing, accounting workflows, and tax reconciliation."
        actions={
          <Button onClick={() => setUpload(true)} disabled={uploadDocuments.isPending}>
            <Upload />
            Upload Documents
          </Button>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <KPICard
          label="Documents"
          value={docs.length}
          hint="received"
          icon={FileStack}
          onClick={() => navigate({ to: "/inbox" })}
        />
        <KPICard
          label="Processed"
          value={processed}
          hint="successfully"
          icon={CheckCircle2}
          tone="success"
          onClick={() => navigate({ to: "/inbox" })}
        />
        <KPICard
          label="Pending Review"
          value={pending}
          hint="in workflow"
          icon={Clock}
          tone="warning"
          onClick={() => navigate({ to: "/review" })}
        />
        <KPICard
          label="Failed"
          value={failed}
          hint="need retry"
          icon={XCircle}
          tone="danger"
          onClick={() => navigate({ to: "/inbox" })}
        />
        <KPICard
          label="Vendors"
          value={vendors.length}
          hint="in master"
          icon={Users}
          onClick={() => navigate({ to: "/vendors" })}
        />
        <KPICard
          label="Reco Exceptions"
          value={exceptions}
          hint="GSTR-2B"
          icon={AlertTriangle}
          tone="warning"
          onClick={() => navigate({ to: "/reconciliation" })}
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Section title="Document Processing Overview" className="xl:col-span-2">
          <div className="mb-3 flex gap-6 text-sm">
            <span>
              <b className="num">{processed}</b>{" "}
              <span className="text-muted-foreground">processed</span>
            </span>
            <span>
              <b className="num">{pending}</b>{" "}
              <span className="text-muted-foreground">pending</span>
            </span>
            <span>
              <b className="num">{failed}</b> <span className="text-muted-foreground">failed</span>
            </span>
          </div>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={activity}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="date" fontSize={11} stroke="var(--muted-foreground)" />
                <YAxis fontSize={11} stroke="var(--muted-foreground)" allowDecimals={false} />
                <Tooltip
                  contentStyle={{
                    fontSize: 12,
                    borderRadius: 6,
                    border: "1px solid var(--border)",
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="processed" stackId="a" fill="var(--chart-1)" name="Processed" />
                <Bar dataKey="pending" stackId="a" fill="var(--chart-4)" name="Pending" />
                <Bar dataKey="failed" stackId="a" fill="var(--chart-5)" name="Failed" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Section>

        <Section
          title="GST Reconciliation Overview"
          actions={
            <Link to="/reconciliation" className="text-xs text-primary hover:underline">
              Open
            </Link>
          }
        >
          {[
            ["Matched invoices", count("Exact Match"), "Exact Match"],
            ["Probable matches", count("Probable Match"), "Probable Match"],
            ["Value mismatches", count("Value Mismatch"), "Value Mismatch"],
            ["Missing in GSTR-2B", count("In Books, Missing in 2B"), "In Books, Missing in 2B"],
            ["Missing in Books", count("In 2B, Missing in Books"), "In 2B, Missing in Books"],
          ].map(([label, n, st]) => (
            <div
              key={label as string}
              className="flex items-center justify-between border-b py-2 text-sm last:border-0"
            >
              <span className="text-muted-foreground">{label}</span>
              <span className="flex items-center gap-2">
                <b className="num">{n}</b>
                <StatusBadge status={st as string} />
              </span>
            </div>
          ))}
        </Section>

        <Section title="Pending Actions">
          {[
            [
              "Invoices requiring review",
              docs.filter((d) => d.status === "Needs Review").length,
              "/invoices",
            ],
            [
              "Unmapped vendors",
              vendors.filter((v) => v.status === "Needs Mapping").length,
              "/vendors",
            ],
            [
              "Missing tax information (TDS PAN)",
              s.tds.filter((t) => t.companyId === s.companyId && !t.pan).length,
              "/tds",
            ],
            ["Reconciliation exceptions", exceptions, "/reconciliation"],
            ["Open review tasks", reviews.filter((r) => r.status === "Open").length, "/review"],
          ].map(([label, n, to]) => (
            <Link
              key={label as string}
              to={to as string}
              className="flex items-center justify-between border-b py-2 text-sm last:border-0 hover:text-primary"
            >
              <span>{label}</span>
              <span className="flex items-center gap-1">
                <b className="num">{n}</b>
                <ArrowRight className="h-3.5 w-3.5" />
              </span>
            </Link>
          ))}
        </Section>

        <Section title="Upcoming Compliance Deadlines">
          <EmptyState
            title="No compliance calendar connected"
            hint="Deadlines will appear here once the backend provides them."
          />
        </Section>

        <Section title="Quick Actions">
          <div className="grid gap-2">
            <Button
              variant="outline"
              className="justify-start"
              onClick={() => setUpload(true)}
              disabled={uploadDocuments.isPending}
            >
              <Upload />
              Upload Documents
            </Button>
            <Button
              variant="outline"
              className="justify-start"
              onClick={() => navigate({ to: "/bank" })}
            >
              <Landmark />
              Upload Bank Statement
            </Button>
            <Button
              variant="outline"
              className="justify-start"
              onClick={() => navigate({ to: "/reconciliation" })}
            >
              <GitCompareArrows />
              Start GST Reconciliation
            </Button>
            <Button
              variant="outline"
              className="justify-start"
              onClick={() => navigate({ to: "/gst" })}
            >
              <Calculator />
              Generate GST Working
            </Button>
            <Button
              variant="outline"
              className="justify-start"
              onClick={() => navigate({ to: "/tds" })}
            >
              <Percent />
              Generate TDS Working
            </Button>
          </div>
        </Section>

        <Section
          title="Recent Activity"
          className="xl:col-span-3"
          actions={
            <Link to="/audit" className="text-xs text-primary hover:underline">
              View audit trail
            </Link>
          }
        >
          {audit.length ? (
            <div className="divide-y">
              {audit.slice(0, 6).map((e) => (
                <div
                  key={e.id}
                  className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
                >
                  <span>
                    <b>{e.user}</b>{" "}
                    <span className="text-muted-foreground">{e.action.toLowerCase()}</span>{" "}
                    {e.recordId} <span className="text-muted-foreground">in {e.module}</span>
                  </span>
                  <span className="num text-xs text-muted-foreground">{e.timestamp}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No activity in this session"
              hint="Actions you take here are recorded locally."
            />
          )}
        </Section>
      </div>

      <Dialog open={upload} onOpenChange={setUpload}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Upload documents</DialogTitle>
            <DialogDescription>
              Files are uploaded to the {company.displayName} inbox for {s.period},{" "}
              {s.financialYear}.
            </DialogDescription>
          </DialogHeader>
          <FileUploadDropzone onFiles={addFiles} />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setUpload(false)}
              disabled={uploadDocuments.isPending}
            >
              Cancel
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
