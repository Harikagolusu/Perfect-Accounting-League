import { useState } from "react";
import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { Bell, ChevronRight, LogOut, Search, Shield, User } from "lucide-react";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { useCompany, useStore } from "@/lib/store";
import { FINANCIAL_YEARS, PERIODS } from "@/lib/constants";
import { useDocuments } from "@/lib/queries/documents";
import { useVendors } from "@/lib/queries/vendors";
import { ALL_NAV } from "./nav";
import { toast } from "sonner";

const selectCls =
  "h-8 rounded-md border border-input bg-background px-2 text-xs font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function AppHeader() {
  const s = useStore();
  const company = useCompany();
  const path = useRouterState({ select: (r) => r.location.pathname });
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const current = ALL_NAV.find((n) => (n.url === "/" ? path === "/" : path.startsWith(n.url)));
  const openReviews = s.reviews.filter((r) => r.companyId === (company?.id ?? "") && r.status === "Open");
  const { data: documents = [] } = useDocuments(company?.id);
  const { data: vendors = [] } = useVendors(company?.id, true);
  const failed = documents.filter((d) => d.status === "Failed");

  return (
    <header className="sticky top-0 z-20 flex flex-col gap-2 border-b bg-background px-3 py-2 lg:flex-row lg:items-center">
      <div className="flex min-w-0 items-center gap-2">
        <SidebarTrigger aria-label="Toggle sidebar" />
        <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
          <Link to="/" className="hover:text-foreground">PAL</Link>
          <ChevronRight className="h-3 w-3" />
          <span className="truncate font-medium text-foreground">{current?.title ?? "Page"}</span>
          {path.split("/").length > 2 && (<><ChevronRight className="h-3 w-3" /><span className="truncate">{decodeURIComponent(path.split("/").pop() || "")}</span></>)}
        </nav>
      </div>
      <div className="flex flex-1 flex-wrap items-center gap-2 lg:justify-end">
        <select aria-label="Company" className={selectCls} value={company?.id ?? ""} onChange={(e) => { s.setCompanyId(e.target.value); toast.success("Company switched", { description: s.companies.find((c) => c.id === e.target.value)?.displayName }); }}>
          {s.companies.map((c) => <option key={c.id} value={c.id}>{c.displayName}</option>)}
        </select>
        <select aria-label="Financial year" className={selectCls} value={s.financialYear} onChange={(e) => s.setFinancialYear(e.target.value)}>
          {FINANCIAL_YEARS.map((f) => <option key={f}>{f}</option>)}
        </select>
        <select aria-label="Reporting period" className={selectCls} value={s.period} onChange={(e) => s.setPeriod(e.target.value)}>
          {PERIODS.map((p) => <option key={p}>{p}</option>)}
        </select>
        <Button variant="outline" size="sm" className="h-8 gap-2 text-muted-foreground" onClick={() => setOpen(true)}>
          <Search className="h-3.5 w-3.5" /> <span className="hidden sm:inline">Search</span>
        </Button>
        <CommandDialog open={open} onOpenChange={setOpen}>
          <CommandInput placeholder="Jump to module, vendor or document…" />
          <CommandList>
            <CommandEmpty>No results.</CommandEmpty>
            <CommandGroup heading="Modules">
              {ALL_NAV.map((n) => (
                <CommandItem key={n.url} onSelect={() => { setOpen(false); navigate({ to: n.url }); }}>
                  <n.icon className="mr-2 h-4 w-4" />{n.title}
                </CommandItem>
              ))}
            </CommandGroup>
            <CommandGroup heading="Vendors">
              {vendors.map((v) => (
                <CommandItem key={v.id} onSelect={() => { setOpen(false); navigate({ to: "/vendors" }); }}>{v.name}</CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </CommandDialog>
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="icon" className="relative h-8 w-8" aria-label="Notifications">
              <Bell className="h-4 w-4" />
              {openReviews.length > 0 && <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] text-destructive-foreground">{openReviews.length}</span>}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-80 p-0">
            <div className="border-b px-3 py-2 text-sm font-semibold">Notifications</div>
            <div className="max-h-72 overflow-auto text-sm">
              <Link to="/review" className="block border-b px-3 py-2 hover:bg-muted">{openReviews.length} items awaiting review</Link>
              <Link to="/inbox" className="block px-3 py-2 hover:bg-muted">{failed.length} documents failed processing</Link>
            </div>
          </PopoverContent>
        </Popover>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-navy text-[10px] text-navy-foreground">LU</span>
              <span className="hidden text-xs md:inline">Local user</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="text-xs">Local session<br /><span className="font-normal text-muted-foreground">Authentication is not connected</span></DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="flex items-center gap-1 text-xs"><Shield className="h-3 w-3" /> Preview role</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={s.role} onValueChange={(v) => s.setRole(v as typeof s.role)}>
              {(["Partner", "Accountant", "Viewer"] as const).map((r) => <DropdownMenuRadioItem key={r} value={r}>{r}</DropdownMenuRadioItem>)}
            </DropdownMenuRadioGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => navigate({ to: "/settings" })}><User className="mr-2 h-4 w-4" />Profile & settings</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => toast.info("Sign-out will be available once authentication is connected.")}><LogOut className="mr-2 h-4 w-4" />Sign out</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
