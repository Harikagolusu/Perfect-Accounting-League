import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { cn } from "@/lib/utils";

export function FileUploadDropzone({
  accept = ".pdf,.jpg,.jpeg,.png,.xlsx,.xls,.csv",
  multiple = true,
  onFiles,
  label = "Drag and drop files here, or click to browse",
  hint = "PDF, JPEG, PNG, XLSX, XLS, CSV",
}: { accept?: string; multiple?: boolean; onFiles: (files: File[]) => void; label?: string; hint?: string }) {
  const ref = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const exts = accept.split(",").map((e) => e.trim().toLowerCase());
  const handle = (list: FileList | null) => {
    if (!list) return;
    const files = Array.from(list).filter((f) => exts.some((e) => f.name.toLowerCase().endsWith(e)));
    if (files.length) onFiles(files);
  };
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => ref.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && ref.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); handle(e.dataTransfer.files); }}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed px-4 py-8 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        over ? "border-primary bg-secondary" : "border-border hover:border-primary/50",
      )}
    >
      <UploadCloud className="h-7 w-7 text-primary" />
      <p className="text-sm font-medium text-foreground">{label}</p>
      <p className="text-xs text-muted-foreground">{hint}</p>
      <input ref={ref} type="file" className="hidden" accept={accept} multiple={multiple} onChange={(e) => { handle(e.target.files); e.target.value = ""; }} />
    </div>
  );
}
