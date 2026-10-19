import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Inbox, Info, TrendingDown, TrendingUp } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-col gap-3 border-b pb-4 md:flex-row md:items-end md:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-foreground">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function KPICard({
  label, value, hint, icon: Icon, trend, tone = "default", onClick,
}: {
  label: string; value: ReactNode; hint?: string; icon: LucideIcon; trend?: number;
  tone?: "default" | "success" | "warning" | "danger"; onClick?: () => void;
}) {
  const toneCls = {
    default: "text-primary bg-secondary",
    success: "text-success bg-success/10",
    warning: "text-warning bg-warning/10",
    danger: "text-destructive bg-destructive/10",
  }[tone];
  const Comp = onClick ? "button" : "div";
  return (
    <Comp
      onClick={onClick}
      className={cn(
        "flex w-full flex-col gap-2 rounded-md border bg-card p-4 text-left",
        onClick && "transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
        <span className={cn("flex h-7 w-7 items-center justify-center rounded", toneCls)}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <div className="num text-2xl font-semibold text-foreground">{value}</div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {trend != null && (
          <span className={cn("inline-flex items-center gap-0.5 font-medium", trend >= 0 ? "text-success" : "text-destructive")}>
            {trend >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
            {Math.abs(trend)}%
          </span>
        )}
        {hint}
      </div>
    </Comp>
  );
}

const STATUS_TONE: Record<string, "success" | "warning" | "danger" | "info" | "neutral"> = {
  Active: "success", Approved: "success", Exported: "success", Generated: "success", Success: "success",
  "Exact Match": "success", Reviewed: "success", Connected: "success", Extracted: "info",
  Onboarding: "info", Processing: "info", Queued: "neutral", Received: "neutral", Draft: "neutral",
  Suggested: "info", "Probable Match": "info", Upcoming: "neutral", Open: "info",
  "Needs Review": "warning", "Needs Mapping": "warning", Pending: "warning", "Due soon": "warning",
  "Value Mismatch": "warning", "Tax Head Mismatch": "warning", "Correction Requested": "warning", Medium: "warning",
  Failed: "danger", Rejected: "danger", Exception: "danger", Unmapped: "danger", High: "danger",
  "In Books, Missing in 2B": "danger", "In 2B, Missing in Books": "danger", Inactive: "neutral", Low: "neutral",
  "Not Available": "neutral", "Demo only": "neutral", "Not connected": "neutral",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge variant={STATUS_TONE[status] ?? "neutral"} className="whitespace-nowrap">{status}</Badge>;
}

export function ConfidenceBadge({ value }: { value: number }) {
  const v = value >= 85 ? "success" : value >= 70 ? "warning" : "danger";
  return <Badge variant={v} className="num">{value}%</Badge>;
}

export function DemoNotice({ children }: { children?: ReactNode }) {
  return (
    <div className="mb-4 flex items-start gap-2 rounded-md border border-primary/20 bg-secondary px-3 py-2 text-xs text-secondary-foreground">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{children ?? "Sample data. Figures are fictional and for demonstration only."}</span>
    </div>
  );
}

export function EmptyState({ title = "No records found", hint }: { title?: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <Inbox className="h-8 w-8 text-muted-foreground" />
      <p className="text-sm font-medium text-foreground">{title}</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function Section({ title, actions, children, className }: { title: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-md border bg-card", className)}>
      <div className="flex items-center justify-between border-b px-4 py-2.5">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {actions}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Field({ label, children, error }: { label: string; children: ReactNode; error?: string }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
      {error && <span className="text-xs text-destructive">{error}</span>}
    </label>
  );
}

export function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b py-2 text-sm last:border-0">
      <span className="text-muted-foreground">{k}</span>
      <span className="text-right font-medium text-foreground">{v}</span>
    </div>
  );
}
