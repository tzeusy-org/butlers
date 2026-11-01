import { useState } from "react";

import { submitHomePersonMappings } from "@/api/client";
import type { HomePersonMappingInput, HomePersonMappingReceipt } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

function opaqueKey(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

const emptyMapping = (): HomePersonMappingInput => ({ ha_person_id: "", entity_id: "" });

export function HomePersonMappingPanel() {
  const [mappings, setMappings] = useState<HomePersonMappingInput[]>([emptyMapping()]);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<HomePersonMappingReceipt | null>(null);
  const [failed, setFailed] = useState(false);

  const clearPrivateFields = () => setMappings([emptyMapping()]);

  const update = (index: number, field: keyof HomePersonMappingInput, value: string) => {
    setMappings((current) =>
      current.map((mapping, row) => (row === index ? { ...mapping, [field]: value } : mapping)),
    );
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setFailed(false);
    setReceipt(null);
    try {
      const response = await submitHomePersonMappings(mappings, opaqueKey());
      setReceipt(response.data);
    } catch {
      setFailed(true);
    } finally {
      clearPrivateFields();
      setBusy(false);
    }
  };

  return (
    <form className="space-y-3" onSubmit={submit} data-testid="home-person-mapping-form">
      <p className="text-sm text-muted-foreground">
        Map exact Home Assistant person IDs to existing person entity UUIDs. This creates only new
        mappings; remap and delete are unavailable.
      </p>
      {mappings.map((mapping, index) => (
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]" key={index}>
          <Input
            aria-label={`Home Assistant person ID ${index + 1}`}
            autoComplete="off"
            spellCheck={false}
            placeholder="person.example"
            value={mapping.ha_person_id}
            onChange={(event) => update(index, "ha_person_id", event.target.value)}
            disabled={busy}
          />
          <Input
            aria-label={`Person entity UUID ${index + 1}`}
            autoComplete="off"
            spellCheck={false}
            placeholder="00000000-0000-0000-0000-000000000000"
            value={mapping.entity_id}
            onChange={(event) => update(index, "entity_id", event.target.value)}
            disabled={busy}
          />
          <Button
            type="button"
            variant="ghost"
            disabled={busy || mappings.length === 1}
            onClick={() => setMappings((current) => current.filter((_, row) => row !== index))}
          >
            Remove
          </Button>
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={busy || mappings.length >= 50}
          onClick={() => setMappings((current) => [...current, emptyMapping()])}
        >
          Add mapping
        </Button>
        <Button
          type="submit"
          disabled={busy || mappings.some((mapping) => !mapping.ha_person_id || !mapping.entity_id)}
        >
          {busy ? "Submitting..." : "Submit mappings"}
        </Button>
        <Button type="button" variant="ghost" disabled={busy} onClick={clearPrivateFields}>
          Clear
        </Button>
      </div>
      {receipt && (
        <p className="text-sm" role="status">
          Complete. Created {receipt.created_count}; unchanged {receipt.unchanged_count}.
        </p>
      )}
      {failed && (
        <p className="text-sm text-destructive" role="alert">
          Mapping request was not applied. Check the values and try again.
        </p>
      )}
    </form>
  );
}
