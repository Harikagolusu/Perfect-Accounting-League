// Export service abstraction. CSV and review XML are generated client-side.
// Client-format XLSX workbooks are fetched from the backend and saved with
// `downloadBlob`; `exportFile` reports "unsupported" instead of faking one.

export type ExportFormat = "CSV" | "XML" | "XLSX";

function escapeCsv(v: unknown) {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(rows: Record<string, unknown>[], columns?: string[]) {
  if (!rows.length && !columns) return "";
  const cols = columns ?? Object.keys(rows[0] ?? {});
  return [cols.join(","), ...rows.map((r) => cols.map((c) => escapeCsv(r[c])).join(","))].join(
    "\n",
  );
}

export function downloadText(filename: string, content: string, mime = "text/csv;charset=utf-8") {
  downloadBlob(filename, new Blob([content], { type: mime }));
}

/** Trigger a browser download for a file already fetched from the backend. */
export function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Returns number of records written, or null if the format is not supported in the browser. */
export function exportFile(
  format: ExportFormat,
  filename: string,
  rows: Record<string, unknown>[],
  columns?: string[],
): number | null {
  if (format === "CSV") {
    downloadText(`${filename}.csv`, toCsv(rows, columns));
    return rows.length;
  }
  if (format === "XML") {
    // Minimal Tally-style envelope for review. Backend will produce the import-ready file.
    const body = rows
      .map(
        (r) =>
          `  <ROW>${Object.entries(r)
            .map(([k, v]) => `<${k}>${String(v ?? "").replace(/[<&>]/g, "")}</${k}>`)
            .join("")}</ROW>`,
      )
      .join("\n");
    downloadText(
      `${filename}.xml`,
      `<?xml version="1.0"?>\n<ENVELOPE>\n${body}\n</ENVELOPE>`,
      "application/xml",
    );
    return rows.length;
  }
  return null;
}

/** Multi-sheet workbook simulation: one CSV per sheet, downloaded in sequence. */
export function exportSheets(
  base: string,
  sheets: { name: string; rows: Record<string, unknown>[] }[],
) {
  sheets.forEach((s, i) =>
    setTimeout(() => downloadText(`${base} - ${i + 1} ${s.name}.csv`, toCsv(s.rows)), i * 250),
  );
  return sheets.length;
}
