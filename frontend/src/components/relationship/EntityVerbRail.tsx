/**
 * EntityVerbRail: the entity operator verbs, inline on the record.
 *
 * bu-6t8ix.4. Entity detail and Plex used to expose notes, interactions, and
 * gifts as read-only lists, so "log an interaction" and "capture a gift idea"
 * had nowhere to write and bu-86c4c.15 (PR #2894) shipped none of them rather
 * than wire a button to nothing. A third verb, "draft a reach-out", shipped
 * alongside these and was retired in bu-2jtfw.11 (replaced by the
 * prepared-action mechanism, surfaced on the insight digest rather than this
 * rail). Each remaining verb here calls a real endpoint that writes a real
 * fact into the relationship butler's own store.
 *
 * Honesty rules this component keeps:
 *   - Every affordance is HONEST-PENDING. Nothing renders as saved until the
 *     server confirms, because each of these is a durable assertion about a
 *     relationship, not a reversible toggle.
 *   - A duplicate is reported as a duplicate. The backend answers 409 rather
 *     than writing the same record twice, and the form says so instead of
 *     showing a generic failure.
 */

import { useState } from "react";

import { verbErrorMessage } from "@/components/relationship/verb-error-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  useCreateEntityGift,
  useCreateEntityInteraction,
  useCreateEntityNote,
} from "@/hooks/use-entities";

/** Interaction types offered by the log-interaction verb. */
const INTERACTION_TYPES = ["call", "message", "email", "meeting", "visit"] as const;

/** Shared status line: pending, saved, or the reason nothing was saved. */
function VerbStatus({
  testId,
  isPending,
  isSuccess,
  successText,
  error,
  alreadyExists,
}: {
  testId: string;
  isPending: boolean;
  isSuccess: boolean;
  successText: string;
  error: unknown;
  alreadyExists: string;
}) {
  if (isPending) {
    return (
      <p className="text-muted-foreground text-xs" data-testid={`${testId}-pending`}>
        Saving...
      </p>
    );
  }
  if (error) {
    return (
      <p className="text-destructive text-xs" data-testid={`${testId}-error`}>
        {verbErrorMessage(error, alreadyExists)}
      </p>
    );
  }
  if (isSuccess) {
    return (
      <p className="text-xs text-[var(--green)]" data-testid={`${testId}-success`}>
        {successText}
      </p>
    );
  }
  return null;
}

/** Log an interaction that already happened. */
function LogInteractionForm({ entityId }: { entityId: string }) {
  const [type, setType] = useState<string>(INTERACTION_TYPES[0]);
  const [summary, setSummary] = useState("");
  const logInteraction = useCreateEntityInteraction();

  const canSubmit = !logInteraction.isPending;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    logInteraction.mutate(
      {
        entityId,
        request: { type, summary: summary.trim() || null },
      },
      { onSuccess: () => setSummary("") },
    );
  }

  return (
    <form className="space-y-2" onSubmit={handleSubmit} aria-label="Log an interaction">
      <div className="flex gap-2">
        <Select value={type} onValueChange={setType}>
          <SelectTrigger
            id={`log-interaction-type-${entityId}`}
            aria-label="Interaction type"
            className="w-32"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {INTERACTION_TYPES.map((t) => (
              <SelectItem key={t} value={t}>
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          aria-label="What happened"
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          placeholder="What happened?"
        />
      </div>
      <Button type="submit" variant="outline" size="sm" disabled={!canSubmit}>
        Save interaction
      </Button>
      <VerbStatus
        testId="log-interaction"
        isPending={logInteraction.isPending}
        isSuccess={logInteraction.isSuccess}
        successText="Interaction logged."
        error={logInteraction.error}
        alreadyExists="An interaction of this type is already logged for that day."
      />
    </form>
  );
}

/** Capture a gift idea before it is forgotten. */
function GiftIdeaForm({ entityId }: { entityId: string }) {
  const [description, setDescription] = useState("");
  const [occasion, setOccasion] = useState("");
  const addGift = useCreateEntityGift();

  const trimmed = description.trim();
  const canSubmit = trimmed.length > 0 && !addGift.isPending;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    addGift.mutate(
      {
        entityId,
        request: { description: trimmed, occasion: occasion.trim() || null },
      },
      {
        onSuccess: () => {
          setDescription("");
          setOccasion("");
        },
      },
    );
  }

  return (
    <form className="space-y-2" onSubmit={handleSubmit} aria-label="Capture a gift idea">
      <div className="flex gap-2">
        <Input
          aria-label="Gift idea"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Gift idea"
        />
        <Input
          aria-label="Occasion"
          value={occasion}
          onChange={(e) => setOccasion(e.target.value)}
          placeholder="Occasion"
          className="w-36"
        />
      </div>
      <Button type="submit" variant="outline" size="sm" disabled={!canSubmit}>
        Save gift idea
      </Button>
      <VerbStatus
        testId="gift-idea"
        isPending={addGift.isPending}
        isSuccess={addGift.isSuccess}
        successText="Gift idea saved."
        error={addGift.error}
        alreadyExists="That gift idea is already on the list."
      />
    </form>
  );
}

/** Record a note about the entity. */
function NoteForm({ entityId }: { entityId: string }) {
  const [content, setContent] = useState("");
  const addNote = useCreateEntityNote();

  const trimmed = content.trim();
  const canSubmit = trimmed.length > 0 && !addNote.isPending;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    addNote.mutate(
      { entityId, request: { content: trimmed } },
      { onSuccess: () => setContent("") },
    );
  }

  return (
    <form className="space-y-2" onSubmit={handleSubmit} aria-label="Add a note">
      <Textarea
        aria-label="Note"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="Something worth remembering"
        rows={2}
      />
      <Button type="submit" variant="outline" size="sm" disabled={!canSubmit}>
        Save note
      </Button>
      <VerbStatus
        testId="entity-note"
        isPending={addNote.isPending}
        isSuccess={addNote.isSuccess}
        successText="Note saved."
        error={addNote.error}
        alreadyExists="You already recorded that note."
      />
    </form>
  );
}

const VERBS = [
  { key: "log-interaction", label: "Log interaction" },
  { key: "gift-idea", label: "Gift idea" },
  { key: "note", label: "Note" },
] as const;

type VerbKey = (typeof VERBS)[number]["key"];

/**
 * The verb rail: three chips, one open form at a time.
 *
 * Collapsed by default so the record still reads as a record. `compact` drops
 * the section heading for the Plex dossier, where the surrounding rail already
 * supplies one and horizontal space is scarce.
 */
export function EntityVerbRail({
  entityId,
  compact = false,
}: {
  entityId: string;
  compact?: boolean;
}) {
  const [open, setOpen] = useState<VerbKey | null>(null);

  return (
    <section className="space-y-2" data-testid="entity-verb-rail">
      {!compact && (
        <h3 className="text-sm font-semibold uppercase tracking-wide">Record something</h3>
      )}
      <div className="flex flex-wrap gap-1.5">
        {VERBS.map((verb) => (
          <Button
            key={verb.key}
            type="button"
            variant={open === verb.key ? "secondary" : "outline"}
            size="sm"
            aria-expanded={open === verb.key}
            data-testid={`verb-chip-${verb.key}`}
            onClick={() => setOpen((current) => (current === verb.key ? null : verb.key))}
          >
            {verb.label}
          </Button>
        ))}
      </div>
      {open === "log-interaction" && <LogInteractionForm entityId={entityId} />}
      {open === "gift-idea" && <GiftIdeaForm entityId={entityId} />}
      {open === "note" && <NoteForm entityId={entityId} />}
    </section>
  );
}
