import { Link, useRouterState } from "@tanstack/react-router";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel,
  SidebarHeader, SidebarMenu, SidebarMenuButton, SidebarMenuItem, useSidebar,
} from "@/components/ui/sidebar";
import { NAV } from "./nav";
import { useStore } from "@/lib/store";
import { ShieldCheck } from "lucide-react";

export function PalMonogram({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect width="32" height="32" rx="6" fill="var(--primary)" />
      <path d="M9 23V9h6.2a4.3 4.3 0 0 1 0 8.6H12.4" stroke="var(--primary-foreground)" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M19 23h5" stroke="var(--primary-foreground)" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  );
}

export function AppSidebar() {
  const { state } = useSidebar();
  const collapsed = state === "collapsed";
  const path = useRouterState({ select: (r) => r.location.pathname });
  const { role } = useStore();
  const isActive = (url: string) => (url === "/" ? path === "/" : path === url || path.startsWith(url + "/"));

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="border-b border-sidebar-border">
        <Link to="/" className="flex items-center gap-2.5 px-1 py-1.5">
          <PalMonogram className="h-8 w-8 shrink-0" />
          {!collapsed && (
            <div className="leading-tight">
              <div className="text-sm font-semibold tracking-wide text-sidebar-accent-foreground">PAL</div>
              <div className="text-[11px] text-sidebar-foreground/70">Perfect Accounting League</div>
            </div>
          )}
        </Link>
      </SidebarHeader>
      <SidebarContent>
        {NAV.map((g) => (
          <SidebarGroup key={g.group} className="py-1">
            <SidebarGroupLabel className="text-[10px] uppercase tracking-wider text-sidebar-foreground/50">{g.group}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {g.items.map((item) => {
                  const restricted = role === "Viewer" && item.url === "/settings";
                  return (
                    <SidebarMenuItem key={item.url}>
                      <SidebarMenuButton
                        asChild
                        isActive={isActive(item.url)}
                        tooltip={item.title}
                        className="data-[active=true]:bg-sidebar-primary data-[active=true]:text-sidebar-primary-foreground"
                      >
                        <Link to={item.url} aria-disabled={restricted}>
                          <item.icon />
                          <span>{item.title}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter className="border-t border-sidebar-border">
        {!collapsed ? (
          <div className="flex items-center gap-2 px-1 py-1 text-[11px] text-sidebar-foreground/70">
            <ShieldCheck className="h-3.5 w-3.5" /> Accuracy. Integrity. Growth.
          </div>
        ) : (
          <ShieldCheck className="mx-auto h-4 w-4 text-sidebar-foreground/70" />
        )}
      </SidebarFooter>
    </Sidebar>
  );
}
