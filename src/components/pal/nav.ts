import {
  LayoutDashboard, Building2, Inbox, FileText, Landmark, Users, Database, BookOpen,
  Calculator, Percent, GitCompareArrows, FileBarChart, ClipboardCheck, History, Settings,
  type LucideIcon,
} from "lucide-react";

export type NavItem = { title: string; url: "/" | "/companies" | "/inbox" | "/invoices" | "/bank" | "/vendors" | "/tally" | "/zoho" | "/gst" | "/tds" | "/reconciliation" | "/reports" | "/review" | "/audit" | "/settings"; icon: LucideIcon };

export const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Overview", items: [{ title: "Dashboard", url: "/", icon: LayoutDashboard }, { title: "Companies & Clients", url: "/companies", icon: Building2 }] },
  {
    group: "Processing",
    items: [
      { title: "Document Inbox", url: "/inbox", icon: Inbox },
      { title: "Invoice Processing", url: "/invoices", icon: FileText },
      { title: "Bank Statements", url: "/bank", icon: Landmark },
      { title: "Vendor Master", url: "/vendors", icon: Users },
    ],
  },
  { group: "Integrations", items: [{ title: "Tally Integration", url: "/tally", icon: Database }, { title: "Zoho Books", url: "/zoho", icon: BookOpen }] },
  {
    group: "Tax & Compliance",
    items: [
      { title: "GST Workings", url: "/gst", icon: Calculator },
      { title: "TDS Workings", url: "/tds", icon: Percent },
      { title: "GST Reconciliation", url: "/reconciliation", icon: GitCompareArrows },
    ],
  },
  {
    group: "Control",
    items: [
      { title: "Reports & Exports", url: "/reports", icon: FileBarChart },
      { title: "Review Queue", url: "/review", icon: ClipboardCheck },
      { title: "Audit Trail", url: "/audit", icon: History },
      { title: "Settings", url: "/settings", icon: Settings },
    ],
  },
];

export const ALL_NAV = NAV.flatMap((g) => g.items);
