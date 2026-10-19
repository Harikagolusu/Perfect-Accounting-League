import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import {
  FileStack, CheckCircle2, Clock, XCircle, Users, AlertTriangle, Upload, Landmark, GitCompareArrows,
  Calculator, Percent, ArrowRight,
} from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from "recharts";
import { DemoNotice, KPICard, PageHeader, Section, StatusBadge } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FileUploadDropzone } from "@/components/pal/dropzone";
import { useCompany, useScoped, useStore } from "@/lib/store";
import { complianceDeadlines, processingActivity } from "@/lib/mock-data";
import { fmtDate } from "@/lib/format";
import { seo } from "@/lib/seo";
import { toast } from "sonner";
import type { InboxDocument } from "@/lib/types";

export const Route = createFileRoute("/")({
  head: () => seo("Accounting Operations", "Monitor document processing, accounting workflows and tax reconciliation."),
  component: Dashboard,
});

function Dashboard() {
  const s = useStore();
  const company = useCompany();
  const navigate = useNavigate();
  const docs = useScoped(s.documents);
  const vendors = useScoped(s.vendors);
  const reco = useScoped(s.reco);
  const reviews = useScoped(s.reviews);
  const audit = useScoped(s.audit);
  const [upload, setUpload] = useState(false);

  const processed = docs.filter((d) => ["Approved", "Exported", "Extracted"].includes(d.status)).length;
  const pending = docs.filter((d) => ["Needs Review", "Queued", "Processing", "Received"].includes(d.status)).length;
  const failed = docs.filter((d) => d.status === "Failed").length;
  const exceptions = reco.filter((r) => r.status !== "Exact Match").length;
  const count = (st: string) => reco.filter((r) => r.status === st).length;

  const addFiles = (files: File[]) => {
    const now = new Date().toISOString().slice(0, 10);
    const newDocs: InboxDocument[] = files.map((f, i) => ({
      id: `D-NEW-${Date.now()}-${i}`, companyId: s.companyId, name: f.name, source: "Manual Upload", receivedAt: now,
      fileType: f.name.split(".").pop()?.toUpperCase() ?? "FILE", status: "Queued", reviewer: "Unassigned", size: f.size,
    }));
    s.setDocuments((d) => [...newDocs, ...d]);
    toast.success(`${files.length} file(s) added to the inbox`, { description: "Queued for processing (demo)." });
    setUpload(false);
  };

  return (
    <>
      <PageHeader
        title="Accounting Operations"
        description="Monitor document processing, accounting workflows, and tax reconciliation."
        actions={<Button onClick={() => setUpload(true)}><Upload />Upload Documents</Button>}
      />
      <DemoNotice>Sample data for <b>{company.displayName}</b> · {s.financialYear} · {s.period}. Figures are fictional.</DemoNotice>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <KPICard label="Documents" value={docs.length} hint="received" icon={FileStack} trend={8} onClick={() => navigate({ to: "/inbox" })} />
        <KPICard label="Processed" value={processed} hint="successfully" icon={CheckCircle2} tone="success" trend={12} onClick={() => navigate({ to: "/inbox" })} />
        <KPICard label="Pending Review" value={pending} hint="in workflow" icon={Clock} tone="warning" onClick={() => navigate({ to: "/review" })} />
        <KPICard label="Failed" value={failed} hint="need retry" icon={XCircle} tone="danger" trend={-3} onClick={() => navigate({ to: "/inbox" })} />
        <KPICard label="Vendors" value={vendors.length} hint="in master" icon={Users} onClick={() => navigate({ to: "/vendors" })} />
        <KPICard label="Reco Exceptions" value={exceptions} hint="GSTR-2B" icon={AlertTriangle} tone="warning" onClick={() => navigate({ to: "/reconciliation" })} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Section title="Document Processing Overview" className="xl:col-span-2">
          <div className="mb-3 flex gap-6 text-sm">
            <span><b className="num">{processed}</b> <span className="text-muted-foreground">processed</span></span>
            <span><b className="num">{pending}</b> <span className="text-muted-foreground">pending</span></span>
            <span><b className="num">{failed}</b> <span className="text-muted-foreground">failed</span></span>
          </div>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={processingActivity}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="date" fontSize={11} stroke="var(--muted-foreground)" />
                <YAxis fontSize={11} stroke="var(--muted-foreground)" />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6, border: "1px solid var(--border)" }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="processed" stackId="a" fill="var(--chart-1)" name="Processed" />
                <Bar dataKey="pending" stackId="a" fill="var(--chart-4)" name="Pending" />
                <Bar dataKey="failed" stackId="a" fill="var(--chart-5)" name="Failed" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Section>

        <Section title="GST Reconciliation Overview" actions={<Link to="/reconciliation" className="text-xs text-primary hover:underline">Open</Link>}>
          {[
            ["Matched invoices", count("Exact Match"), "Exact Match"],
            ["Probable matches", count("Probable Match"), "Probable Match"],
            ["Value mismatches", count("Value Mismatch"), "Value Mismatch"],
            ["Missing in GSTR-2B", count("In Books, Missing in 2B"), "In Books, Missing in 2B"],
            ["Missing in Books", count("In 2B, Missing in Books"), "In 2B, Missing in Books"],
          ].map(([label, n, st]) => (
            <div key={label as string} className="flex items-center justify-between border-b py-2 text-sm last:border-0">
              <span className="text-muted-foreground">{label}</span>
              <span className="flex items-center gap-2"><b className="num">{n}</b><StatusBadge status={st as string} /></span>
            </div>
          ))}
        </Section>

        <Section title="Pending Actions">
          {[
            ["Invoices requiring review", docs.filter((d) => d.status === "Needs Review").length, "/invoices"],
            ["Unmapped vendors", vendors.filter((v) => v.status === "Needs Mapping").length, "/vendors"],
            ["Missing tax information (TDS PAN)", s.tds.filter((t) => t.companyId === s.companyId && !t.pan).length, "/tds"],
            ["Reconciliation exceptions", exceptions, "/reconciliation"],
            ["Open review tasks", reviews.filter((r) => r.status === "Open").length, "/review"],
          ].map(([label, n, to]) => (
            <Link key={label as string} to={to as string} className="flex items-center justify-between border-b py-2 text-sm last:border-0 hover:text-primary">
              <span>{label}</span>
              <span className="flex items-center gap-1"><b className="num">{n}</b><ArrowRight className="h-3.5 w-3.5" /></span>
            </Link>
          ))}
        </Section>

        <Section title="Upcoming Compliance Deadlines">
          <table className="w-full text-sm">
            <tbody>
              {complianceDeadlines.map((d) => (
                <tr key={d.type} className="border-b last:border-0">
                  <td className="py-2">{d.type}<div className="text-xs text-muted-foreground">{company.displayName}</div></td>
                  <td className="num py-2 text-right text-xs">{fmtDate(d.due)}</td>
                  <td className="py-2 pl-2 text-right"><StatusBadge status={d.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>

        <Section title="Quick Actions">
          <div className="grid gap-2">
            <Button variant="outline" className="justify-start" onClick={() => setUpload(true)}><Upload />Upload Documents</Button>
            <Button variant="outline" className="justify-start" onClick={() => navigate({ to: "/bank" })}><Landmark />Upload Bank Statement</Button>
            <Button variant="outline" className="justify-start" onClick={() => navigate({ to: "/reconciliation" })}><GitCompareArrows />Start GST Reconciliation</Button>
            <Button variant="outline" className="justify-start" onClick={() => navigate({ to: "/gst" })}><Calculator />Generate GST Working</Button>
            <Button variant="outline" className="justify-start" onClick={() => navigate({ to: "/tds" })}><Percent />Generate TDS Working</Button>
          </div>
        </Section>

        <Section title="Recent Activity" className="xl:col-span-3" actions={<Link to="/audit" className="text-xs text-primary hover:underline">View audit trail</Link>}>
          <div className="divide-y">
            {audit.slice(0, 6).map((e) => (
              <div key={e.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <span><b>{e.user}</b> <span className="text-muted-foreground">{e.action.toLowerCase()}</span> {e.recordId} <span className="text-muted-foreground">in {e.module}</span></span>
                <span className="num text-xs text-muted-foreground">{e.timestamp}</span>
              </div>
            ))}
          </div>
        </Section>
      </div>

      <Dialog open={upload} onOpenChange={setUpload}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Upload documents</DialogTitle>
            <DialogDescription>Files are added to the {company.displayName} inbox for {s.period}, {s.financialYear}.</DialogDescription>
          </DialogHeader>
          <FileUploadDropzone onFiles={addFiles} />
          <DialogFooter><Button variant="outline" onClick={() => setUpload(false)}>Cancel</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
