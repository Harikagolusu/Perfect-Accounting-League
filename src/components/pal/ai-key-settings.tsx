/**
 * Settings section for the DeepSeek API key. Install-wide, not per company:
 * every company's extraction and ledger matching uses this key.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Field, InfoNotice } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { aiKeyApi, type AiKeyStatusDto } from "@/lib/api/aiKey";

const KEY = ["ai-key"] as const;

export function AiKeySettings({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({
    queryKey: KEY,
    queryFn: aiKeyApi.get,
    enabled: typeof window !== "undefined",
  });
  const [draft, setDraft] = useState("");
  const onDone = (dto: AiKeyStatusDto) => queryClient.setQueryData(KEY, dto);
  const save = useMutation({
    mutationFn: aiKeyApi.save,
    onSuccess: (dto) => {
      onDone(dto);
      setDraft("");
      toast.success("DeepSeek key verified and saved");
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const remove = useMutation({
    mutationFn: aiKeyApi.remove,
    onSuccess: (dto) => {
      onDone(dto);
      toast.info("Saved key removed");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const status = isPending
    ? "Checking…"
    : !data?.configured
      ? "Not configured. Document extraction and AI ledger matching are off."
      : data.source === "settings"
        ? `In use: ${data.masked} (saved here)`
        : `In use: ${data.masked} (from the server .env file)`;

  return (
    <div className="space-y-4">
      <InfoNotice>
        Get a key at platform.deepseek.com → API keys. PAL checks the key with DeepSeek before
        saving it, stores it encrypted on the server and never shows it again.
      </InfoNotice>
      <p className="text-sm">{status}</p>
      <fieldset disabled={!canManage || save.isPending} className="space-y-3 border-0 p-0">
        <Field label="DeepSeek API key">
          <Input
            type="password"
            autoComplete="off"
            placeholder="sk-…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        </Field>
        <div className="flex gap-2">
          <Button disabled={draft.trim().length < 8} onClick={() => save.mutate(draft.trim())}>
            {save.isPending
              ? "Verifying…"
              : data?.source === "settings"
                ? "Replace key"
                : "Save key"}
          </Button>
          {data?.source === "settings" && (
            <Button variant="outline" disabled={remove.isPending} onClick={() => remove.mutate()}>
              Remove saved key
            </Button>
          )}
        </div>
      </fieldset>
    </div>
  );
}
