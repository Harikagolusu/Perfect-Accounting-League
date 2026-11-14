import { createFileRoute } from "@tanstack/react-router";
import { UserCircle } from "lucide-react";
import { PageHeader, Section, KV, InfoNotice, StatusBadge } from "@/components/pal/common";
import { useCompany, useStore } from "@/lib/store";
import { seo } from "@/lib/seo";

export const Route = createFileRoute("/profile")({
  head: () => seo("Profile", "Your account and current workspace."),
  component: ProfilePage,
});

function ProfilePage() {
  const s = useStore();
  const company = useCompany();

  return (
    <>
      <PageHeader title="Profile" description="Your account and current workspace." />
      <InfoNotice>
        Authentication is not connected yet, so this is the local session. Your role controls what you can see and change.
      </InfoNotice>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Section title="Account">
          <div className="mb-3 flex items-center gap-3">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
              <UserCircle className="h-7 w-7 text-muted-foreground" />
            </span>
            <div>
              <div className="text-sm font-semibold">Local user</div>
              <div className="text-xs text-muted-foreground">Local session</div>
            </div>
          </div>
          <KV k="Role" v={<StatusBadge status={s.role} />} />
          <KV k="Access" v={s.role === "Viewer" ? "Read-only" : "Edit"} />
        </Section>
        <Section title="Workspace">
          <KV k="Company" v={company?.displayName ?? "—"} />
          <KV k="GSTIN" v={company?.gstin || "—"} />
          <KV k="Financial year" v={s.financialYear} />
          <KV k="Period" v={s.period} />
        </Section>
      </div>
    </>
  );
}
