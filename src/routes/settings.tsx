import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Lock, Plus } from "lucide-react";
import {
  PageHeader,
  Section,
  Field,
  StatusBadge,
  InfoNotice,
  EmptyState,
} from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useStore } from "@/lib/store";
import { tdsRates } from "@/lib/constants";
import { EMAIL_RE } from "@/lib/format";
import { cn } from "@/lib/utils";
import { seo } from "@/lib/seo";
import { toast } from "sonner";

export const Route = createFileRoute("/settings")({
  head: () =>
    seo(
      "Settings",
      "Organization, users, roles, tax rules, tolerances, notifications and security.",
    ),
  component: SettingsPage,
});

type FieldDef = {
  key: string;
  label: string;
  type?: "text" | "number" | "email" | "toggle" | "select";
  options?: string[];
  required?: boolean;
};
const SECTIONS: { id: string; title: string; fields?: FieldDef[] }[] = [
  {
    id: "org",
    title: "Organization Settings",
    fields: [
      { key: "firm", label: "Firm name", required: true },
      { key: "frn", label: "Firm registration no. (ICAI)" },
      { key: "email", label: "Firm email", type: "email", required: true },
      { key: "city", label: "City" },
    ],
  },
  { id: "users", title: "User Management" },
  { id: "roles", title: "Roles and Permissions" },
  {
    id: "company",
    title: "Company Preferences",
    fields: [
      {
        key: "defaultSoftware",
        label: "Default accounting software",
        type: "select",
        options: ["Tally Prime", "Zoho Books"],
      },
      { key: "autoAssign", label: "Auto-assign reviewers", type: "toggle" },
      { key: "maskPan", label: "Mask PAN/GSTIN in lists", type: "toggle" },
    ],
  },
  {
    id: "fy",
    title: "Financial Year Settings",
    fields: [
      { key: "fyStart", label: "FY start month", type: "select", options: ["April", "January"] },
      { key: "lockPrev", label: "Lock previous FY for edits", type: "toggle" },
    ],
  },
  {
    id: "templates",
    title: "Document Templates",
    fields: [
      { key: "invoicePrefix", label: "Export file prefix" },
      {
        key: "dateFormat",
        label: "Date format",
        type: "select",
        options: ["DD-MM-YYYY", "YYYY-MM-DD"],
      },
    ],
  },
  {
    id: "ledger",
    title: "Ledger Mapping Rules",
    fields: [
      { key: "autoApprove", label: "Auto-approve threshold (%)", type: "number", required: true },
      { key: "reviewBelow", label: "Send to review below (%)", type: "number", required: true },
      { key: "keywordMatch", label: "Enable keyword matching", type: "toggle" },
    ],
  },
  {
    id: "gst",
    title: "GST Configuration",
    fields: [
      {
        key: "gstFreq",
        label: "Return frequency",
        type: "select",
        options: ["Monthly", "Quarterly (QRMP)"],
      },
      { key: "rcm", label: "Track reverse charge", type: "toggle" },
    ],
  },
  {
    id: "tds",
    title: "TDS Rule Configuration",
    fields: Object.keys(tdsRates).map((k) => ({
      key: `tds${k}`,
      label: `Section ${k} rate (%)`,
      type: "number" as const,
    })),
  },
  {
    id: "tol",
    title: "Reconciliation Tolerances",
    fields: [
      { key: "taxTol", label: "Tax difference tolerance (₹)", type: "number" },
      { key: "dateTol", label: "Date tolerance (days)", type: "number" },
      { key: "normalize", label: "Normalize invoice numbers", type: "toggle" },
    ],
  },
  {
    id: "notify",
    title: "Notification Preferences",
    fields: [
      { key: "nDeadline", label: "Compliance deadline reminders", type: "toggle" },
      { key: "nReview", label: "New review assignments", type: "toggle" },
      { key: "nFailed", label: "Failed document alerts", type: "toggle" },
    ],
  },
  {
    id: "email",
    title: "Email Integration Settings",
    fields: [
      { key: "inbox", label: "Invoice intake address", type: "email" },
      {
        key: "provider",
        label: "Provider",
        type: "select",
        options: ["Google Workspace", "Microsoft 365", "IMAP"],
      },
    ],
  },
  {
    id: "security",
    title: "Security Settings",
    fields: [
      { key: "mfa", label: "Require two-factor authentication", type: "toggle" },
      { key: "session", label: "Session timeout (minutes)", type: "number" },
      { key: "ipAllow", label: "Restrict access by IP allowlist", type: "toggle" },
    ],
  },
];
const DEFAULTS: Record<string, string | boolean> = {
  firm: "",
  frn: "",
  email: "",
  city: "",
  defaultSoftware: "Tally Prime",
  autoAssign: true,
  maskPan: false,
  fyStart: "April",
  lockPrev: true,
  invoicePrefix: "PAL",
  dateFormat: "DD-MM-YYYY",
  autoApprove: "95",
  reviewBelow: "80",
  keywordMatch: true,
  gstFreq: "Monthly",
  rcm: true,
  taxTol: "10",
  dateTol: "3",
  normalize: true,
  nDeadline: true,
  nReview: true,
  nFailed: true,
  inbox: "",
  provider: "Google Workspace",
  mfa: true,
  session: "30",
  ipAllow: false,
  ...Object.fromEntries(Object.entries(tdsRates).map(([k, v]) => [`tds${k}`, String(v)])),
};
const PERMS = ["View data", "Edit records", "Approve", "Export", "Manage settings"];
const ROLE_PERMS: Record<string, boolean[]> = {
  Partner: [true, true, true, true, true],
  Accountant: [true, true, true, true, false],
  Viewer: [true, false, false, false, false],
};

function SettingsPage() {
  const s = useStore();
  const [active, setActive] = useState("org");
  const [vals, setVals] = useState(DEFAULTS);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState(false);
  const [users, setUsers] = useState<{ name: string; role: string; active: boolean }[]>([]);
  const [newUser, setNewUser] = useState("");
  const sec = SECTIONS.find((x) => x.id === active)!;

  if (s.role === "Viewer") {
    return (
      <>
        <PageHeader title="Settings" />
        <div className="flex flex-col items-center gap-2 rounded-md border bg-card py-16 text-center">
          <Lock className="h-8 w-8 text-muted-foreground" />
          <p className="font-medium">Permission denied</p>
          <p className="text-sm text-muted-foreground">
            Your role (Viewer) cannot manage settings. Contact a Partner.
          </p>
        </div>
      </>
    );
  }

  const validate = () => {
    const e: Record<string, string> = {};
    sec.fields?.forEach((f) => {
      const v = vals[f.key];
      if (f.required && !String(v ?? "").trim()) e[f.key] = "Required";
      if (f.type === "email" && v && !EMAIL_RE.test(String(v))) e[f.key] = "Invalid email";
      if (f.type === "number" && (isNaN(Number(v)) || Number(v) < 0))
        e[f.key] = "Enter a positive number";
    });
    if (active === "ledger" && Number(vals.reviewBelow) > Number(vals.autoApprove))
      e.reviewBelow = "Must be ≤ auto-approve threshold";
    setErrors(e);
    return !Object.keys(e).length;
  };

  return (
    <>
      <PageHeader
        title="Settings"
        description="Firm-wide configuration. Changes are kept in this browser session only."
      />
      <InfoNotice>
        Passwords, API keys and credentials are never stored in the browser. Secrets will be managed
        by the backend.
      </InfoNotice>
      <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
        <nav className="flex gap-1 overflow-x-auto rounded-md border bg-card p-2 lg:flex-col">
          {SECTIONS.map((x) => (
            <button
              key={x.id}
              onClick={() => {
                setActive(x.id);
                setErrors({});
              }}
              className={cn(
                "whitespace-nowrap rounded px-3 py-2 text-left text-sm",
                active === x.id
                  ? "bg-secondary font-medium text-secondary-foreground"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              {x.title}
            </button>
          ))}
        </nav>
        <Section title={sec.title}>
          {sec.fields && (
            <>
              <div className="grid gap-4 md:grid-cols-2">
                {sec.fields.map((f) =>
                  f.type === "toggle" ? (
                    <label
                      key={f.key}
                      className="flex items-center justify-between gap-3 rounded border p-3 text-sm"
                    >
                      {f.label}
                      <Switch
                        checked={!!vals[f.key]}
                        onCheckedChange={(v) => setVals({ ...vals, [f.key]: v })}
                      />
                    </label>
                  ) : f.type === "select" ? (
                    <Field key={f.key} label={f.label}>
                      <select
                        className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                        value={String(vals[f.key])}
                        onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })}
                      >
                        {f.options!.map((o) => (
                          <option key={o}>{o}</option>
                        ))}
                      </select>
                    </Field>
                  ) : (
                    <Field
                      key={f.key}
                      label={f.label + (f.required ? " *" : "")}
                      error={errors[f.key]}
                    >
                      <Input
                        type={f.type === "number" ? "number" : (f.type ?? "text")}
                        value={String(vals[f.key] ?? "")}
                        onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })}
                      />
                    </Field>
                  ),
                )}
              </div>
              {active === "email" && (
                <p className="mt-3 text-xs text-muted-foreground">
                  Mailbox OAuth connection will be completed by the backend.{" "}
                  <StatusBadge status="Not connected" />
                </p>
              )}
              <div className="mt-4 flex gap-2">
                <Button onClick={() => validate() && setConfirm(true)}>Save changes</Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setVals(DEFAULTS);
                    setErrors({});
                    toast.info("Changes discarded");
                  }}
                >
                  Reset
                </Button>
              </div>
            </>
          )}
          {active === "users" && (
            <div>
              <div className="mb-3 flex gap-2">
                <Input
                  placeholder="Full name"
                  value={newUser}
                  onChange={(e) => setNewUser(e.target.value)}
                  className="h-9 max-w-xs"
                />
                <Button
                  disabled={!newUser.trim()}
                  onClick={() => {
                    setUsers([
                      ...users,
                      { name: newUser.trim(), role: "Accountant", active: true },
                    ]);
                    setNewUser("");
                    toast.success("User added to this browser session");
                  }}
                >
                  <Plus />
                  Add user
                </Button>
              </div>
              {users.length ? (
                <table className="w-full text-sm">
                  <thead className="text-xs text-muted-foreground">
                    <tr>
                      <th className="py-2 text-left">User</th>
                      <th className="text-left">Role</th>
                      <th className="text-left">Active</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u, i) => (
                      <tr key={u.name} className="border-t">
                        <td className="py-2">{u.name}</td>
                        <td>
                          <select
                            className="h-8 rounded border border-input bg-background px-1 text-sm"
                            value={u.role}
                            onChange={(e) =>
                              setUsers(
                                users.map((x, j) => (j === i ? { ...x, role: e.target.value } : x)),
                              )
                            }
                          >
                            {Object.keys(ROLE_PERMS).map((r) => (
                              <option key={r}>{r}</option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <Switch
                            checked={u.active}
                            onCheckedChange={(v) =>
                              setUsers(users.map((x, j) => (j === i ? { ...x, active: v } : x)))
                            }
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState
                  title="No users configured"
                  hint="User accounts and invitations will be provided by the backend."
                />
              )}
            </div>
          )}
          {active === "roles" && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="py-2 text-left">Role</th>
                    {PERMS.map((p) => (
                      <th key={p} className="px-2 text-center">
                        {p}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(ROLE_PERMS).map(([r, ps]) => (
                    <tr key={r} className="border-t">
                      <td className="py-2 font-medium">{r}</td>
                      {ps.map((p, i) => (
                        <td key={i} className="text-center">
                          {p ? "✓" : "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-3 text-xs text-muted-foreground">
                Permissions are enforced server-side. Use the profile menu to preview the app as
                each role.
              </p>
            </div>
          )}
        </Section>
      </div>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Save {sec.title.toLowerCase()}?</AlertDialogTitle>
            <AlertDialogDescription>
              These changes apply for this browser session only.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                toast.success("Settings saved");
              }}
            >
              Save
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
