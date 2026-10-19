import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { ArrowLeft, Pencil, CheckCircle2 } from "lucide-react";
import { PageHeader, Section, KV, StatusBadge, EmptyState } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { useStore } from "@/lib/store";
import { fmtDate } from "@/lib/format";
import { CompanyDialog } from "./companies.index";

export const Route = createFileRoute("/companies/$id")({
  head: () => ({
    meta: [
      { title: "Company details · PAL" },
      { name: "description", content: "Company overview, GST and PAN details, integrations and recent activity." },
      { property: "og:title", content: "Company details · PAL" },
      { property: "og:description", content: "Company overview, GST and PAN details, integrations and recent activity." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: CompanyDetail,
});

function CompanyDetail() {
  const { id } = Route.useParams();
  const s = useStore();
  const [edit, setEdit] = useState(false);
  const c = s.companies.find((x) => x.id === id);
  if (!c) return <EmptyState title="Company not found" hint="It may have been removed." />;
  const docs = s.documents.filter((d) => d.companyId === id);
  const reco = s.reco.filter((r) => r.companyId === id);
  const vendorCount = s.vendors.filter((v) => v.companyId === id).length;

  return (
    <>
      <Link to="/companies" className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3 w-3" />All companies</Link>
      <PageHeader
        title={c.displayName}
        description={c.legalName}
        actions={<>
          {s.companyId !== c.id && <Button variant="outline" onClick={() => s.setCompanyId(c.id)}><CheckCircle2 />Make active company</Button>}
          <Button onClick={() => setEdit(true)}><Pencil />Edit</Button>
        </>}
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Section title="Company Overview">
          <KV k="Status" v={<StatusBadge status={c.status} />} />
          <KV k="State" v={c.state} />
          <KV k="Address" v={c.address} />
          <KV k="Contact" v={c.contactPerson} />
          <KV k="Email" v={c.email} />
          <KV k="Phone" v={c.phone} />
          <KV k="Financial Year" v={c.financialYear} />
        </Section>
        <Section title="GST & PAN">
          <KV k="GSTIN" v={<span className="num">{c.gstin}</span>} />
          <KV k="PAN" v={<span className="num">{c.pan}</span>} />
          <KV k="State code" v={c.gstin.slice(0, 2)} />
          <KV k="Vendors in master" v={vendorCount} />
        </Section>
        <Section title="Accounting Integrations">
          <KV k="Primary software" v={c.software} />
          <KV k="Tally Prime" v={<StatusBadge status={c.software === "Tally Prime" ? "Demo only" : "Not connected"} />} />
          <KV k="Zoho Books" v={<StatusBadge status={c.software === "Zoho Books" ? "Demo only" : "Not connected"} />} />
          <p className="mt-2 text-xs text-muted-foreground">Live connections are configured by your engineering team.</p>
        </Section>
        <Section title="Recent Documents" className="lg:col-span-2">
          {docs.slice(0, 6).map((d) => (
            <div key={d.id} className="flex items-center justify-between border-b py-2 text-sm last:border-0">
              <span>{d.name}</span><span className="flex items-center gap-3"><span className="text-xs text-muted-foreground">{fmtDate(d.receivedAt)}</span><StatusBadge status={d.status} /></span>
            </div>
          ))}
        </Section>
        <Section title="Assigned Users">
          {c.assignedUsers.length ? c.assignedUsers.map((u) => <div key={u} className="border-b py-2 text-sm last:border-0">{u}</div>) : <EmptyState title="No users assigned" />}
        </Section>
        <Section title="Recent Reconciliation Activity" className="lg:col-span-3">
          {reco.filter((r) => r.status !== "Exact Match").slice(0, 5).map((r) => (
            <div key={r.id} className="flex items-center justify-between border-b py-2 text-sm last:border-0">
              <span>{r.supplier} · <span className="num">{r.invoiceNo}</span></span><StatusBadge status={r.status} />
            </div>
          ))}
        </Section>
      </div>
      {edit && <CompanyDialog initial={c} open={edit} onOpenChange={setEdit} />}
    </>
  );
}
