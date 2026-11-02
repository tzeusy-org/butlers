/**
 * ReplyTemplateDraftsPanel: the owner's editor for standing-reply template
 * drafts in the Trust Console (bu-jv1moa.42, over the bu-jv1moa.10 API).
 *
 * A draft is a typed description of a reply the owner may later let go out
 * without a per-event tap. Here it can be authored, edited, inspected,
 * revoked and previewed, and nothing more: drafts carry no authority, every
 * preview says `authority: none`, and there is deliberately no activation
 * control (activation belongs to bu-jv1moa.28).
 */

import { useId, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";

import { getEntities, getEntity } from "@/api/index.ts";
import type {
  StandingReplyTemplateDraft,
  StandingReplyTemplatePreview,
} from "@/api/index.ts";
import { useButlers } from "@/hooks/use-butlers";
import {
  useCreateReplyTemplateDraft,
  useEditReplyTemplateDraft,
  usePreviewReplyTemplateDraft,
  useReplyTemplateDrafts,
  useRevokeReplyTemplateDraft,
} from "@/hooks/use-reply-template-drafts.ts";
import { QueryBoundary, SourceDegradedNote } from "@/components/ui/query-boundary.tsx";
import { Time } from "@/components/ui/time";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Grammar mirrored from src/butlers/modules/approvals/reply_templates.py
// ---------------------------------------------------------------------------

/** The send tools a draft may describe, with their recipient and content arguments. */
const SEND_TOOL_SHAPES = {
  telegram_send_message: { recipientArg: "chat_id", contentArgs: ["text"] },
  email_send_message: { recipientArg: "to", contentArgs: ["subject", "body"] },
} as const satisfies Record<string, { recipientArg: string; contentArgs: readonly string[] }>;

type SendTool = keyof typeof SEND_TOOL_SHAPES;
const SEND_TOOLS = Object.keys(SEND_TOOL_SHAPES) as SendTool[];
const SLOT_TYPES = ["date", "time", "datetime", "duration", "amount", "count"] as const;
const SLOT_RE = /\{([^{}]*)\}/g;

function isSendTool(name: string): name is SendTool {
  return name in SEND_TOOL_SHAPES;
}

interface DetectedSlot {
  source: string;
  name: string;
  type: string;
  valid: boolean;
}

/** Slots as the owner types them, so a bad type shows before the save round-trip. */
function detectSlots(template: string): DetectedSlot[] {
  return [...template.matchAll(SLOT_RE)].map((m) => {
    const [name = "", type = ""] = m[1].split(":");
    return {
      source: m[0],
      name,
      type,
      valid:
        /^[a-z][a-z0-9_]{0,31}$/.test(name) &&
        (SLOT_TYPES as readonly string[]).includes(type),
    };
  });
}

// ---------------------------------------------------------------------------
// Error and reason copy: the API's stable codes, in words the owner can act on
// ---------------------------------------------------------------------------

const ERROR_COPY: Record<string, string> = {
  adjacent_slots: "Two slots touch. Put fixed text between them.",
  content_arguments_mismatch: "Fill a template for every content field of this tool.",
  draft_not_found: "This draft no longer exists.",
  draft_revoked: "This draft is revoked and can no longer change.",
  idempotency_key_conflict: "This save is already in progress. Wait a moment.",
  idempotency_key_reused: "This save was already used for different content. Reopen the editor.",
  owner_dashboard_session_required: "Only the owner's dashboard session can manage drafts.",
  personal_account_send_forbidden: "Personal account send tools are never allowed.",
  recipient_unresolved: "A recipient no longer resolves to a known entity.",
  recipients_required: "Pin at least one recipient.",
  slot_duplicate: "A slot name is used twice in one template.",
  slot_malformed: "A slot is malformed. Write slots as {name:type}.",
  slot_type_forbidden: `A slot uses a forbidden type. Allowed: ${SLOT_TYPES.join(", ")}.`,
  stale_revision: "This draft changed since you opened it. Save again to replace the newer revision, or cancel to see it.",
  template_empty: "A template is empty.",
  template_too_long: "A template is longer than 2,000 characters.",
  template_without_fixed_text: "A template needs some fixed text, not only slots.",
  too_many_recipients: "Pin at most 20 recipients.",
  unsupported_send_tool: "This send tool is not supported.",
};

const REASON_COPY: Record<string, string> = {
  draft_revoked: "the draft is revoked",
  tool_mismatch: "the call uses a different send tool",
  unknown_parameters: "the call has arguments the draft does not cover",
  recipient_unresolved: "the recipient does not resolve to a known entity",
  recipient_not_pinned: "the recipient is not one of the pinned recipients",
  content_incomplete: "a content field is missing",
  content_mismatch: "the content does not fit the templates",
};

/** The API's machine code from `{detail: {error}}`, else the plain message. */
function errorCode(error: unknown): string {
  // Duck-typed (ApiError carries `detail`) so the panel never depends on the class identity.
  const detail = (error as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object") {
    const code = (detail as Record<string, unknown>).error;
    if (typeof code === "string") return code;
  }
  return error instanceof Error ? error.message : String(error);
}

function errorCopy(error: unknown): string {
  const code = errorCode(error);
  return ERROR_COPY[code] ?? code;
}

const EYEBROW = "text-[10px] font-mono uppercase tracking-widest text-muted-foreground";
const FIELD =
  "w-full px-2 py-1.5 text-sm border border-border rounded bg-background focus:outline-none focus:ring-focus focus:border-focus";
const SECONDARY_BUTTON =
  "text-xs font-mono px-2 py-1 border border-border rounded hover:border-foreground/40 transition-colors disabled:opacity-50 max-md:min-h-11 max-md:min-w-11";
const PRIMARY_BUTTON =
  "px-3 py-1.5 text-sm bg-foreground text-background rounded hover:opacity-90 disabled:opacity-50 transition-opacity";

function AuthorityNone() {
  return (
    <span
      data-testid="authority-none"
      className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground border border-border rounded px-1.5 py-0.5"
    >
      authority: none
    </span>
  );
}

// ---------------------------------------------------------------------------
// Recipients
// ---------------------------------------------------------------------------

function useRecipientName(entityId: string, known?: string) {
  const { data } = useQuery({
    queryKey: ["approvals", "reply-template-recipient", entityId],
    queryFn: () => getEntity(entityId, { facts_limit: 1 }),
    enabled: known === undefined,
    staleTime: 5 * 60_000,
  });
  return known ?? data?.data.canonical_name ?? `${entityId.slice(0, 8)}…`;
}

function RecipientName({ entityId, known }: { entityId: string; known?: string }) {
  return <>{useRecipientName(entityId, known)}</>;
}

function RecipientPicker({
  value,
  names,
  onChange,
}: {
  value: string[];
  names: Record<string, string>;
  onChange: (ids: string[], names: Record<string, string>) => void;
}) {
  const inputId = useId();
  const [q, setQ] = useState("");
  const term = q.trim();
  const search = useQuery({
    queryKey: ["approvals", "reply-template-recipient-search", term],
    queryFn: () => getEntities({ q: term, limit: 8 }),
    enabled: term.length >= 2,
    staleTime: 30_000,
  });
  // search.isError renders its own message below, never an empty result.
  const results = search.data ? search.data.data.filter((e) => !value.includes(e.id)) : [];

  return (
    <div>
      <label htmlFor={inputId} className={cn(EYEBROW, "block mb-1")}>
        Recipients (pinned entities)
      </label>
      {value.length > 0 && (
        <ul className="flex flex-wrap gap-1.5 mb-2" aria-label="Pinned recipients">
          {value.map((id) => (
            <li
              key={id}
              className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-xs"
            >
              <RecipientName entityId={id} known={names[id]} />
              <button
                type="button"
                aria-label={`Unpin ${names[id] ?? id}`}
                onClick={() => onChange(value.filter((v) => v !== id), names)}
                className="text-muted-foreground hover:text-destructive px-0.5"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        id={inputId}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search people and entities"
        autoComplete="off"
        className={FIELD}
      />
      {term.length >= 2 && (
        <div className="mt-1 text-xs" aria-live="polite">
          {search.isLoading ? (
            <span className="font-mono text-muted-foreground">searching…</span>
          ) : search.isError ? (
            <span className="text-destructive">Entity search unavailable.</span>
          ) : results.length === 0 ? (
            <span className="text-muted-foreground">No matching entities.</span>
          ) : (
            <ul className="border border-border rounded divide-y divide-border/50">
              {results.map((entity) => (
                <li key={entity.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onChange([...value, entity.id], {
                        ...names,
                        [entity.id]: entity.canonical_name,
                      });
                      setQ("");
                    }}
                    className="w-full text-left px-2 py-1.5 hover:bg-muted focus-visible:bg-muted focus-visible:ring-focus"
                  >
                    {entity.canonical_name}{" "}
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {entity.entity_type}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Editor: author (create) or edit one draft
// ---------------------------------------------------------------------------

function DraftEditor({
  draft,
  onDone,
}: {
  /** The draft to edit; omitted when authoring a new one. */
  draft?: StandingReplyTemplateDraft;
  onDone: () => void;
}) {
  const formId = useId();
  const butlers = useButlers();
  const createMut = useCreateReplyTemplateDraft();
  const editMut = useEditReplyTemplateDraft();
  // One key per editor session: a double-submit replays instead of duplicating.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [butler, setButler] = useState(draft?.butler ?? "");
  const [tool, setTool] = useState<SendTool>(
    draft && isSendTool(draft.tool_name) ? draft.tool_name : "telegram_send_message",
  );
  const [templates, setTemplates] = useState<Record<string, string>>(draft?.templates ?? {});
  const [recipients, setRecipients] = useState<string[]>(draft?.recipient_entity_ids ?? []);
  const [names, setNames] = useState<Record<string, string>>({});
  const [description, setDescription] = useState(draft?.description ?? "");
  const [localError, setLocalError] = useState<string | null>(null);

  const mutation = draft ? editMut : createMut;
  const contentArgs = SEND_TOOL_SHAPES[tool].contentArgs;
  // butlers.isError renders a degraded note under the select.
  const butlerNames = butlers.data ? butlers.data.data.map((b) => b.name).sort() : [];

  function submit(e: FormEvent) {
    e.preventDefault();
    const content = Object.fromEntries(contentArgs.map((arg) => [arg, templates[arg] ?? ""]));
    if (!draft && !butler) return setLocalError("Choose the butler that owns this draft.");
    if (Object.values(content).some((t) => t.trim() === ""))
      return setLocalError(ERROR_COPY.template_empty);
    if (contentArgs.some((arg) => detectSlots(content[arg]).some((s) => !s.valid)))
      return setLocalError(ERROR_COPY.slot_type_forbidden);
    if (recipients.length === 0) return setLocalError(ERROR_COPY.recipients_required);
    setLocalError(null);
    if (draft) {
      editMut.mutate(
        {
          id: draft.id,
          request: {
            expected_revision: draft.revision,
            templates: content,
            recipient_entity_ids: recipients,
            description,
          },
        },
        { onSuccess: onDone },
      );
    } else {
      createMut.mutate(
        {
          butler,
          tool_name: tool,
          templates: content,
          recipient_entity_ids: recipients,
          description,
          idempotency_key: idempotencyKey,
        },
        { onSuccess: onDone },
      );
    }
  }

  const shownError = localError ?? (mutation.isError ? errorCopy(mutation.error) : null);

  return (
    <form
      onSubmit={submit}
      aria-labelledby={`${formId}-title`}
      data-testid="reply-template-draft-editor"
      className="mt-4 space-y-3 border border-border rounded p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 id={`${formId}-title`} className="text-sm font-medium">
          {draft ? `Edit draft · revision ${draft.revision}` : "New standing-reply draft"}
        </h3>
        <AuthorityNone />
      </div>
      <p className="text-xs text-muted-foreground">
        Saving keeps this an inactive draft. It sends, approves and authorizes nothing.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label htmlFor={`${formId}-butler`} className={cn(EYEBROW, "block mb-1")}>
            Butler
          </label>
          <select
            id={`${formId}-butler`}
            value={butler}
            disabled={draft !== undefined || !butlers.data}
            onChange={(e) => setButler(e.target.value)}
            className={FIELD}
          >
            {draft ? (
              <option value={draft.butler}>{draft.butler}</option>
            ) : (
              <>
                <option value="">Select a butler</option>
                {butlerNames.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </>
            )}
          </select>
          {butlers.isError && (
            <SourceDegradedNote
              label="Butlers"
              onRetry={() => void butlers.refetch()}
              className="mt-1"
            />
          )}
        </div>
        <fieldset>
          <legend className={cn(EYEBROW, "mb-1")}>Send tool</legend>
          <div className="flex flex-wrap gap-3 text-sm py-1.5">
            {SEND_TOOLS.map((name) => (
              <label key={name} className="inline-flex items-center gap-1.5 font-mono text-xs">
                <input
                  type="radio"
                  name={`${formId}-tool`}
                  value={name}
                  checked={tool === name}
                  disabled={draft !== undefined}
                  onChange={() => setTool(name)}
                />
                {name}
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      {contentArgs.map((arg) => {
        const slots = detectSlots(templates[arg] ?? "");
        return (
          <div key={arg}>
            <label htmlFor={`${formId}-tpl-${arg}`} className={cn(EYEBROW, "block mb-1")}>
              {arg} template
            </label>
            <textarea
              id={`${formId}-tpl-${arg}`}
              rows={arg === "body" ? 4 : 2}
              value={templates[arg] ?? ""}
              onChange={(e) => setTemplates((t) => ({ ...t, [arg]: e.target.value }))}
              aria-describedby={`${formId}-tpl-${arg}-hint`}
              className={cn(FIELD, "font-mono text-xs")}
            />
            <p id={`${formId}-tpl-${arg}-hint`} className="mt-1 text-xs text-muted-foreground">
              {slots.length === 0 ? (
                <>
                  Fixed text plus typed slots such as <code>{"{when:time}"}</code>. Types:{" "}
                  {SLOT_TYPES.join(", ")}.
                </>
              ) : (
                <>
                  Slots:{" "}
                  {slots.map((s, i) => (
                    <span key={`${s.source}-${i}`}>
                      {i > 0 && ", "}
                      <code className={s.valid ? undefined : "text-destructive"}>{s.source}</code>
                      {!s.valid && <span className="text-destructive"> (not allowed)</span>}
                    </span>
                  ))}
                </>
              )}
            </p>
          </div>
        );
      })}

      <RecipientPicker
        value={recipients}
        names={names}
        onChange={(ids, nextNames) => {
          setRecipients(ids);
          setNames(nextNames);
        }}
      />

      <div>
        <label htmlFor={`${formId}-description`} className={cn(EYEBROW, "block mb-1")}>
          Description (optional)
        </label>
        <input
          id={`${formId}-description`}
          value={description}
          maxLength={500}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="When this reply would fit"
          className={FIELD}
        />
      </div>

      {shownError && (
        <p role="alert" className="text-xs text-destructive">
          {shownError}
        </p>
      )}

      <div className="flex gap-2">
        <button type="submit" disabled={mutation.isPending} className={PRIMARY_BUTTON}>
          {mutation.isPending ? "Saving…" : draft ? "Save revision" : "Save draft"}
        </button>
        <button
          type="button"
          onClick={onDone}
          className="px-3 py-1.5 text-sm border border-border rounded hover:border-foreground/40 transition-colors"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Preview: test a hypothetical call; nothing is sent, parked or approved
// ---------------------------------------------------------------------------

function PreviewResult({ preview }: { preview: StandingReplyTemplatePreview }) {
  const slotEntries = Object.entries(preview.slots).flatMap(([arg, slots]) =>
    Object.entries(slots).map(([slot, value]) => ({ arg, slot, value })),
  );
  return (
    <div
      data-testid="reply-template-preview-result"
      className="mt-3 border-t border-border/50 pt-3 text-sm space-y-1.5"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "font-medium",
            preview.eligible ? "text-foreground" : "text-[var(--amber-text)]",
          )}
        >
          {preview.eligible ? "This call would match the draft." : "This call would not match."}
        </span>
        <AuthorityNone />
      </div>
      {preview.reasons.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-muted-foreground">
          {preview.reasons.map((r) => (
            <li key={r}>{REASON_COPY[r] ?? r}</li>
          ))}
        </ul>
      )}
      {slotEntries.length > 0 && (
        <dl className="font-mono text-xs grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
          {slotEntries.map(({ arg, slot, value }) => (
            <div key={`${arg}.${slot}`} className="contents">
              <dt className="text-muted-foreground">
                {arg}.{slot}
              </dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="text-xs text-muted-foreground">
        Preview only, against revision {preview.revision}. A match grants nothing while the draft
        is inactive.
      </p>
    </div>
  );
}

function DraftPreview({ draft }: { draft: StandingReplyTemplateDraft }) {
  const formId = useId();
  const previewMut = usePreviewReplyTemplateDraft();
  const shape = isSendTool(draft.tool_name) ? SEND_TOOL_SHAPES[draft.tool_name] : null;
  const [args, setArgs] = useState<Record<string, string>>({});
  if (!shape) return null;
  const fields = [shape.recipientArg, ...shape.contentArgs];

  function submit(e: FormEvent) {
    e.preventDefault();
    const toolArgs = Object.fromEntries(fields.map((f) => [f, args[f] ?? ""]));
    previewMut.mutate({
      id: draft.id,
      request: { tool_name: draft.tool_name, tool_args: toolArgs },
    });
  }

  return (
    <form onSubmit={submit} className="mt-4" aria-labelledby={`${formId}-title`}>
      <div className="flex items-center gap-2 mb-2">
        <h4 id={`${formId}-title`} className={EYEBROW}>
          Preview a call
        </h4>
        <span className="text-xs text-muted-foreground">Nothing is sent.</span>
      </div>
      <div className="space-y-2">
        {fields.map((field) => (
          <div key={field}>
            <label htmlFor={`${formId}-${field}`} className="block text-xs font-mono mb-0.5">
              {field}
            </label>
            {field === "body" ? (
              <textarea
                id={`${formId}-${field}`}
                rows={3}
                value={args[field] ?? ""}
                onChange={(e) => setArgs((a) => ({ ...a, [field]: e.target.value }))}
                className={cn(FIELD, "font-mono text-xs")}
              />
            ) : (
              <input
                id={`${formId}-${field}`}
                value={args[field] ?? ""}
                onChange={(e) => setArgs((a) => ({ ...a, [field]: e.target.value }))}
                className={cn(FIELD, "font-mono text-xs")}
              />
            )}
          </div>
        ))}
      </div>
      <button type="submit" disabled={previewMut.isPending} className={cn(SECONDARY_BUTTON, "mt-2")}>
        {previewMut.isPending ? "Checking…" : "Run preview"}
      </button>
      {previewMut.isError && (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {errorCopy(previewMut.error)}
        </p>
      )}
      {previewMut.data && <PreviewResult preview={previewMut.data.data} />}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Inspector: the selected draft in full, with its edit/revoke/preview controls
// ---------------------------------------------------------------------------

function RevokeControl({ draft }: { draft: StandingReplyTemplateDraft }) {
  const [confirming, setConfirming] = useState(false);
  const revokeMut = useRevokeReplyTemplateDraft();
  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className={cn(SECONDARY_BUTTON, "hover:text-destructive")}
      >
        Revoke
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-2">
      <span className="text-xs text-muted-foreground">Revoke for good? It cannot be undone.</span>
      <button
        type="button"
        disabled={revokeMut.isPending}
        onClick={() =>
          revokeMut.mutate(
            { id: draft.id, expectedRevision: draft.revision },
            { onSettled: () => setConfirming(false) },
          )
        }
        className={cn(SECONDARY_BUTTON, "text-destructive border-destructive/40")}
      >
        {revokeMut.isPending ? "Revoking…" : "Confirm revoke"}
      </button>
      <button type="button" onClick={() => setConfirming(false)} className={SECONDARY_BUTTON}>
        Keep
      </button>
      {revokeMut.isError && (
        <span role="alert" className="text-xs text-destructive">
          {errorCopy(revokeMut.error)}
        </span>
      )}
    </span>
  );
}

function DraftInspector({
  draft,
  onEdit,
}: {
  draft: StandingReplyTemplateDraft;
  onEdit: () => void;
}) {
  const revoked = draft.state === "revoked";
  return (
    <div
      data-testid="reply-template-draft-inspector"
      className="px-3 pb-4 pt-1 text-sm space-y-3"
    >
      <dl className="space-y-2">
        {Object.entries(draft.templates).map(([arg, template]) => (
          <div key={arg}>
            <dt className={EYEBROW}>{arg} template</dt>
            <dd className="font-mono text-xs whitespace-pre-wrap mt-0.5">{template}</dd>
          </div>
        ))}
        <div>
          <dt className={EYEBROW}>Recipients</dt>
          <dd className="mt-0.5 text-xs">
            {draft.recipient_entity_ids.map((id, i) => (
              <span key={id}>
                {i > 0 && ", "}
                <RecipientName entityId={id} />
              </span>
            ))}
          </dd>
        </div>
        <div className="font-mono text-[10px] text-muted-foreground">
          created <Time value={draft.created_at} mode="absolute" precision="minute" compact /> ·
          updated <Time value={draft.updated_at} mode="absolute" precision="minute" compact />
          {draft.revoked_at && (
            <>
              {" "}
              · revoked <Time value={draft.revoked_at} mode="absolute" precision="minute" compact />
            </>
          )}
        </div>
      </dl>
      {!revoked && (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={onEdit} className={SECONDARY_BUTTON}>
            Edit
          </button>
          <RevokeControl draft={draft} />
        </div>
      )}
      <DraftPreview key={draft.revision} draft={draft} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

type EditorState = { mode: "closed" } | { mode: "create" } | { mode: "edit"; id: string };

export function ReplyTemplateDraftsPanel() {
  const query = useReplyTemplateDrafts();
  const [selected, setSelected] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState>({ mode: "closed" });

  // query.isError is threaded into the QueryBoundary below.
  const pages = query.data ? query.data.pages : [];
  const drafts = pages.flatMap((p) => p.items);
  const degraded = [...new Set(pages.flatMap((p) => p.sources_degraded ?? []))];
  const editing = editor.mode === "edit" ? drafts.find((d) => d.id === editor.id) : undefined;

  return (
    <section
      className="border-t border-border mt-8 pt-6"
      data-testid="reply-template-drafts-panel"
      aria-labelledby="reply-template-drafts-title"
    >
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 id="reply-template-drafts-title" className={EYEBROW}>
            Standing-reply drafts
          </h2>
          <div className="text-sm text-muted-foreground mt-0.5">
            Typed reply templates kept as inactive drafts. They authorize nothing.
          </div>
        </div>
        {editor.mode === "closed" && (
          <button
            type="button"
            onClick={() => setEditor({ mode: "create" })}
            className={SECONDARY_BUTTON}
          >
            + Draft
          </button>
        )}
      </div>

      {editor.mode === "create" && <DraftEditor onDone={() => setEditor({ mode: "closed" })} />}
      {editing && (
        <DraftEditor
          // Keyed by id only: a newer revision must not wipe the owner's unsaved edit.
          key={editing.id}
          draft={editing}
          onDone={() => setEditor({ mode: "closed" })}
        />
      )}

      {degraded.length > 0 && (
        <SourceDegradedNote
          label="Standing-reply drafts"
          detail={`${degraded.join(", ")} unavailable. The list may be incomplete.`}
          onRetry={() => void query.refetch()}
          className="mt-4"
        />
      )}

      <div className="mt-4">
        <QueryBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error ? new Error(errorCopy(query.error)) : null}
          isEmpty={drafts.length === 0}
          onRetry={() => void query.refetch()}
          sourceLabel="standing-reply drafts"
          loadingFallback={<div className="font-mono text-sm text-muted-foreground">loading…</div>}
          emptyFallback={
            degraded.length === 0 ? (
              <div className="text-sm text-muted-foreground">
                No drafts yet. A draft describes a reply you may later allow without a tap.
              </div>
            ) : null
          }
        >
          <ul className="border border-border rounded divide-y divide-border/50">
            {drafts.map((draft) => {
              const open = selected === draft.id;
              return (
                <li key={draft.id} data-testid="reply-template-draft-row" data-state={draft.state}>
                  <button
                    type="button"
                    aria-expanded={open}
                    onClick={() => setSelected(open ? null : draft.id)}
                    className="w-full text-left px-3 py-2 hover:bg-muted/50 focus:outline-none focus-visible:ring-focus"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs truncate">
                        {draft.butler} · {draft.tool_name}
                      </span>
                      <span
                        className={cn(
                          "font-mono text-[10px] uppercase tracking-wider shrink-0",
                          draft.state === "revoked"
                            ? "text-muted-foreground line-through"
                            : "text-foreground",
                        )}
                      >
                        {draft.state} · r{draft.revision}
                      </span>
                    </div>
                    {draft.description && (
                      <div className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                        {draft.description}
                      </div>
                    )}
                  </button>
                  {open && (
                    <DraftInspector
                      draft={draft}
                      onEdit={() => setEditor({ mode: "edit", id: draft.id })}
                    />
                  )}
                </li>
              );
            })}
          </ul>
          {query.hasNextPage && (
            <button
              type="button"
              onClick={() => void query.fetchNextPage()}
              disabled={query.isFetchingNextPage}
              className={cn(SECONDARY_BUTTON, "mt-2")}
            >
              {query.isFetchingNextPage ? "loading…" : "Load more"}
            </button>
          )}
        </QueryBoundary>
      </div>
    </section>
  );
}
