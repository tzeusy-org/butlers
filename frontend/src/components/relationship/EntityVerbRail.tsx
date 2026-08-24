/**
 * EntityVerbRail: the three entity operator verbs, inline on the record.
 *
 * bu-6t8ix.4. Entity detail and Plex used to expose notes, interactions, and
 * gifts as read-only lists, so "log an interaction", "capture a gift idea",
 * and "draft a reach-out" had nowhere to write and bu-86c4c.15 (PR #2894)
 * shipped none of them rather than wire a button to nothing. Each verb here
 * calls a real endpoint that writes a real fact into the relationship
 * butler's own store.
 *
 * Honesty rules this component keeps:
 *   - Every affordance is HONEST-PENDING. Nothing renders as saved until the
 *     server confirms, because each of these is a durable assertion about a
 *     relationship, not a reversible toggle.
 *   - "Draft" means drafted. The reach-out verb stores text and stops there.
 *     There is no send endpoint behind it and no channel is contacted; the
 *     channel field records who the owner meant to use, not a delivery.
 *   - A duplicate is reported as a duplicate. The backend answers 409 rather
 *     than writing the same record twice, and the form says so instead of
 *     showing a generic failure.
 */

import { useState } from "react";

import { ApiError } from "@/api/client";
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
  useCreateEntityReachOutDraft,
} from "@/hooks/use-entities";

/** Interaction types offered by the log-interaction verb. */
const INTERACTION_TYPES = ["call", "message", "email", "meeting", "visit"] as const;

/** Channels the draft-reach-out verb can record as intent. Nothing is sent. */
const REACH_OUT_CHANNELS = ["telegram", "email", "sms", "in person"] as const;

/**
 * Turn a failed verb write into one honest sentence.
 *
 * The backend answers these routes with a structured `detail` object rather
 * than a bare string, so `ApiError.message` is a JSON blob unless we read the
 * dict ourselves. Three cases are worth naming explicitly:
 *   409 -> the record already exists (dedupe, not failure);
 *   403 -> the owner gate rejected the caller;
 *   422 -> the tool rejected the input (bad direction, reserved type).
 */
export function verbErrorMessage(error: unknown, alreadyExists: string): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error && error.message
      ? error.message
      : "Something went wrong. Nothing was recorded.";
  }
  const detail =
    error.detail && typeof error.detail === "object"
      ? (error.detail as Record<string, unknown>)
      : undefined;
  const detailMessage = typeof detail?.message === "string" ? detail.message : undefined;

  if (error.status === 409) return alreadyExists;
  if (error.status === 403) {
    return "Only the owner can write to this record.";
  }
  if (error.status === 404) {
    return "This entity no longer exists.";
  }
  return detailMessage || error.message || "Nothing was recorded.";
}

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

/** Draft a reach-out. Drafted, never sent. */
function DraftReachOutForm({ entityId }: { entityId: string }) {
  const [message, setMessage] = useState("");
  const [channel, setChannel] = useState<string>(REACH_OUT_CHANNELS[0]);
  const draftReachOut = useCreateEntityReachOutDraft();

  const trimmed = message.trim();
  const canSubmit = trimmed.length > 0 && !draftReachOut.isPending;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    draftReachOut.mutate(
      { entityId, request: { message: trimmed, channel } },
      { onSuccess: () => setMessage("") },
    );
  }

  return (
    <form className="space-y-2" onSubmit={handleSubmit} aria-label="Draft a reach-out">
      <Textarea
        aria-label="Draft message"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder="What do you want to say?"
        rows={2}
      />
      <div className="flex items-center gap-2">
        <Select value={channel} onValueChange={setChannel}>
          <SelectTrigger
            id={`draft-reach-out-channel-${entityId}`}
            aria-label="Channel"
            className="w-32"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {REACH_OUT_CHANNELS.map((c) => (
              <SelectItem key={c} value={c}>
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button type="submit" variant="outline" size="sm" disabled={!canSubmit}>
          Save draft
        </Button>
      </div>
      <p className="text-muted-foreground text-xs" data-testid="draft-reach-out-inert-note">
        Saved as a draft only. Nothing is sent.
      </p>
      <VerbStatus
        testId="draft-reach-out"
        isPending={draftReachOut.isPending}
        isSuccess={draftReachOut.isSuccess}
        successText="Draft saved. Nothing was sent."
        error={draftReachOut.error}
        alreadyExists="You already drafted that message."
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
  { key: "draft-reach-out", label: "Draft reach-out" },
  { key: "note", label: "Note" },
] as const;

type VerbKey = (typeof VERBS)[number]["key"];

/**
 * The verb rail: four chips, one open form at a time.
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
      {open === "draft-reach-out" && <DraftReachOutForm entityId={entityId} />}
      {open === "note" && <NoteForm entityId={entityId} />}
    </section>
  );
}
