import { useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "./common";
import { cn } from "@/lib/utils";

export interface Column<T> {
  key: string;
  header: string;
  cell?: (row: T) => ReactNode;
  sortValue?: (row: T) => string | number;
  align?: "left" | "right";
  className?: string;
}

interface Props<T> {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  searchKeys?: (row: T) => string;
  toolbar?: ReactNode;
  pageSize?: number;
  onRowClick?: (row: T) => void;
  selectable?: boolean;
  bulkActions?: (selected: string[], clear: () => void) => ReactNode;
  searchPlaceholder?: string;
}

export function DataTable<T>({
  rows, columns, rowKey, searchKeys, toolbar, pageSize = 10, onRowClick, selectable, bulkActions, searchPlaceholder = "Search…",
}: Props<T>) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);

  const filtered = useMemo(() => {
    let r = rows;
    if (q && searchKeys) {
      const needle = q.toLowerCase();
      r = r.filter((row) => searchKeys(row).toLowerCase().includes(needle));
    }
    if (sort) {
      const col = columns.find((c) => c.key === sort.key);
      const get = col?.sortValue ?? ((row: T) => String((row as Record<string, unknown>)[sort.key] ?? ""));
      r = [...r].sort((a, b) => {
        const av = get(a), bv = get(b);
        return (av > bv ? 1 : av < bv ? -1 : 0) * sort.dir;
      });
    }
    return r;
  }, [rows, q, sort, columns, searchKeys]);

  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const cur = Math.min(page, pages - 1);
  const visible = filtered.slice(cur * pageSize, cur * pageSize + pageSize);
  const allVisibleSelected = visible.length > 0 && visible.every((r) => selected.includes(rowKey(r)));

  return (
    <div className="rounded-md border bg-card">
      <div className="flex flex-col gap-2 border-b p-3 md:flex-row md:items-center">
        {searchKeys && (
          <div className="relative md:w-72">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              aria-label="Search table"
              value={q}
              onChange={(e) => { setQ(e.target.value); setPage(0); }}
              placeholder={searchPlaceholder}
              className="h-9 pl-8"
            />
          </div>
        )}
        <div className="flex flex-1 flex-wrap items-center gap-2">{toolbar}</div>
        {selectable && selected.length > 0 && bulkActions && (
          <div className="flex items-center gap-2 rounded bg-secondary px-2 py-1 text-xs text-secondary-foreground">
            <span className="font-medium">{selected.length} selected</span>
            {bulkActions(selected, () => setSelected([]))}
          </div>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              {selectable && (
                <th className="w-10 px-3 py-2">
                  <Checkbox
                    aria-label="Select all"
                    checked={allVisibleSelected}
                    onCheckedChange={(v) =>
                      setSelected(v ? Array.from(new Set([...selected, ...visible.map(rowKey)])) : selected.filter((id) => !visible.some((r) => rowKey(r) === id)))
                    }
                  />
                </th>
              )}
              {columns.map((c) => (
                <th key={c.key} className={cn("whitespace-nowrap px-3 py-2 font-medium", c.align === "right" ? "text-right" : "text-left")}>
                  <button
                    className="inline-flex items-center gap-1 hover:text-foreground"
                    onClick={() => setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === 1 ? -1 : 1 } : { key: c.key, dir: 1 }))}
                  >
                    {c.header}
                    {sort?.key === c.key && (sort.dir === 1 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => {
              const id = rowKey(row);
              return (
                <tr
                  key={id}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cn("border-t hover:bg-muted/60", onRowClick && "cursor-pointer")}
                >
                  {selectable && (
                    <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        aria-label={`Select ${id}`}
                        checked={selected.includes(id)}
                        onCheckedChange={(v) => setSelected((s) => (v ? [...s, id] : s.filter((x) => x !== id)))}
                      />
                    </td>
                  )}
                  {columns.map((c) => (
                    <td key={c.key} className={cn("whitespace-nowrap px-3 py-2", c.align === "right" && "num text-right", c.className)}>
                      {c.cell ? c.cell(row) : String((row as Record<string, unknown>)[c.key] ?? "—")}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
        {visible.length === 0 && <EmptyState hint="Try adjusting search or filters." />}
      </div>
      <div className="flex items-center justify-between border-t px-3 py-2 text-xs text-muted-foreground">
        <span>
          {filtered.length === 0 ? 0 : cur * pageSize + 1}–{Math.min(filtered.length, (cur + 1) * pageSize)} of {filtered.length}
        </span>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Previous page" disabled={cur === 0} onClick={() => setPage(cur - 1)}>
            <ChevronLeft />
          </Button>
          <span>Page {cur + 1} of {pages}</span>
          <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Next page" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>
            <ChevronRight />
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Compact select used inside table toolbars. */
export function FilterSelect({
  value, onChange, options, label,
}: { value: string; onChange: (v: string) => void; options: string[]; label: string }) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <option value="All">{label}: All</option>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}
