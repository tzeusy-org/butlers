import { useState } from "react";

import { ApiError } from "@/api/client.ts";
import type { PresenceOwnerEntities } from "@/api/home-presence.ts";
import { Button } from "@/components/ui/button";
import {
  usePresenceOwnerEntities,
  useSavePresenceOwnerEntities,
} from "@/hooks/use-home-presence.ts";

import { Panel } from "@/components/ui/atoms";

const ENTITY_PATTERN = /^[a-z0-9_]+\.[a-z0-9_]+$/;
const MAX_ENTITIES = 50;

/** One id per line, exactly as typed: only blank lines drop, nothing is trimmed or folded. */
function validate(raw: string): string[] | string {
  const ids = raw.split("\n").filter((line) => line !== "");
  if (ids.length > MAX_ENTITIES) return `Use at most ${MAX_ENTITIES} entities.`;
  if (ids.some((id) => id.length > 255 || !ENTITY_PATTERN.test(id))) {
    return "Use exact Home Assistant entity IDs such as person.alex, one per line, with no spaces.";
  }
  if (new Set(ids).size !== ids.length) return "Each entity may appear once.";
  return ids;
}

function failureMessage(error: unknown): string {
  if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
    return "These settings changed since they were loaded. The latest list is shown; edit again.";
  }
  if (error instanceof ApiError && error.code === "INVALID_REFERENCE") {
    const count = Number(
      (error.detail as { invalid_reference_count?: unknown } | undefined)
        ?.invalid_reference_count,
    );
    const subject = Number.isInteger(count) && count > 0 ? `${count} of these entities` : "Some entities";
    return `${subject} have not been seen in Home Assistant yet. Nothing was changed.`;
  }
  return "Could not save the presence entities. Nothing was changed.";
}

function Summary({ settings }: { settings: PresenceOwnerEntities }) {
  if (!settings.configured) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="home-presence-unconfigured">
        Not set. Home will not say whether you are home until you choose your entities.
      </p>
    );
  }
  return (
    <ul className="flex flex-wrap gap-2" data-testid="home-presence-entities">
      {settings.owner_entities.map((id) => (
        <li key={id} className="rounded border border-border px-1.5 font-mono text-xs">
          {id}
        </li>
      ))}
    </ul>
  );
}

/**
 * Owner-only editor for the Home Assistant entities that mean "the owner" (for example their
 * person entity, phone tracker or a room sensor). Saving is a settings change only: the next
 * scheduled presence check reads the new list. The generic state editor can still change the
 * same setting directly; this editor's conflict check covers only its own saves.
 */
export function HomePresenceOwnerPanel() {
  const settings = usePresenceOwnerEntities();
  const save = useSavePresenceOwnerEntities();
  // The draft and the version it was opened from: a refetch while editing must not refresh
  // the compare-and-swap version, or a change made meanwhile would be silently overwritten.
  const [draft, setDraft] = useState<{ text: string; version: number } | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);

  const startEdit = (current: PresenceOwnerEntities) => {
    save.reset();
    setFieldError(null);
    setDraft({ text: current.owner_entities.join("\n"), version: current.version });
  };

  const cancel = () => {
    setDraft(null);
    setFieldError(null);
  };

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (draft === null) return;
    const result = validate(draft.text);
    if (typeof result === "string") {
      setFieldError(result);
      return;
    }
    setFieldError(null);
    save.mutate(
      { ownerEntities: result, expectedVersion: draft.version },
      {
        onSuccess: cancel,
        onError: (error) => {
          // A stale draft cannot be resubmitted as-is; start again from the refreshed list.
          if (error instanceof ApiError && error.code === "VERSION_CONFLICT") setDraft(null);
        },
      },
    );
  };

  return (
    <Panel title="Who counts as you" span={4} testId="home-presence-owner-panel">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          The Home Assistant entities that mean you are home or in a room. Housemates and guests
          never count. Changes apply at the next presence check, within a few minutes.
        </p>

        {settings.isLoading ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Loading presence settings
          </p>
        ) : null}
        {settings.isError ? (
          <div className="flex flex-wrap items-center gap-3" aria-live="polite">
            <p className="text-sm text-destructive" data-testid="home-presence-error">
              Couldn&apos;t load presence settings. Nothing has changed.
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void settings.refetch()}
            >
              Reload
            </Button>
          </div>
        ) : null}

        {settings.data && draft === null ? (
          <div className="flex flex-wrap items-center gap-3">
            <Summary settings={settings.data} />
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="ml-auto"
              onClick={() => startEdit(settings.data)}
            >
              {settings.data.configured ? "Edit entities" : "Choose entities"}
            </Button>
          </div>
        ) : null}

        {settings.data && draft !== null ? (
          <form className="space-y-3" noValidate onSubmit={onSubmit} aria-label="Presence entities">
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="home-presence-entities">
                Entity IDs, one per line (exact, as Home Assistant shows them)
              </label>
              <textarea
                id="home-presence-entities"
                rows={4}
                spellCheck={false}
                autoComplete="off"
                placeholder="person.alex"
                className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-sm"
                value={draft.text}
                onChange={(event) => setDraft({ ...draft, text: event.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                Leave it empty to stop presence detection.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={save.isPending}>
                {save.isPending ? "Saving..." : "Save entities"}
              </Button>
              <Button type="button" variant="outline" onClick={cancel}>
                Cancel
              </Button>
              {fieldError ? (
                <p className="text-sm text-destructive" role="alert">
                  {fieldError}
                </p>
              ) : null}
            </div>
          </form>
        ) : null}

        {save.isError ? (
          <p className="text-sm text-destructive" role="alert">
            {failureMessage(save.error)}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
