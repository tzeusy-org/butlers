/**
 * Conversation rename and reversible archive (REQ-dashboard-chat-ui-017).
 *
 * Every metadata change goes through the existing owner-authenticated
 * `PATCH /api/butlers/{name}/conversations/{id}` contract with exactly one
 * field: a title-only rename or a status-only archive/unarchive. The list
 * row, the shared conversation header and the command palette all call the
 * same actions here, so they share one optimistic cache path, one
 * duplicate-submission guard and one feedback vocabulary.
 *
 * Cache discipline: the optimistic patch and its rollback touch only the
 * target conversation's row in each cached list, search result and identity
 * lookup. A rollback reinserts that row's last confirmed value at its old
 * position, so it never overwrites another conversation or newer evidence.
 * Every settlement then refreshes the affected queries, because a timeout or
 * lost response may still have applied the change on the server.
 */

import { useMemo, useSyncExternalStore } from "react";
import { useQuery, useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { toast } from "sonner";

import { getConversationById, updateConversation } from "@/api/index.ts";
import type {
  ConversationSummary,
  ConversationUpdateRequest,
} from "@/api/index.ts";
import { conversationKeys } from "./conversation-keys.ts";

// ---------------------------------------------------------------------------
// Title validation
// ---------------------------------------------------------------------------

/** Server bound on `title` (ConversationUpdateRequest, max_length=500). */
export const CONVERSATION_TITLE_MAX = 500;

export type TitleValidation = { ok: true; title: string } | { ok: false; error: string };

/** Trim, then require 1 through 500 Unicode code points. */
export function validateConversationTitle(raw: string): TitleValidation {
  const title = raw.trim();
  const length = Array.from(title).length;
  if (length === 0) {
    return { ok: false, error: `Enter a title of 1 to ${CONVERSATION_TITLE_MAX} characters.` };
  }
  if (length > CONVERSATION_TITLE_MAX) {
    return {
      ok: false,
      error: `Title is ${length} characters. Use ${CONVERSATION_TITLE_MAX} or fewer.`,
    };
  }
  return { ok: true, title };
}

export function conversationTitle(conversation: Pick<ConversationSummary, "title">): string {
  return conversation.title ?? "Untitled conversation";
}

// ---------------------------------------------------------------------------
// Pending guard (shared across row, header and palette)
// ---------------------------------------------------------------------------

let pendingIds: ReadonlySet<string> = new Set();
const pendingListeners = new Set<() => void>();

function setPending(conversationId: string, isPending: boolean) {
  const next = new Set(pendingIds);
  if (isPending) next.add(conversationId);
  else next.delete(conversationId);
  pendingIds = next;
  for (const listener of pendingListeners) listener();
}

function subscribePending(listener: () => void) {
  pendingListeners.add(listener);
  return () => {
    pendingListeners.delete(listener);
  };
}

const getPendingSnapshot = () => pendingIds;

/** True while a metadata PATCH for this conversation has not settled. */
export function useConversationMetadataPending(conversationId: string | null | undefined): boolean {
  const ids = useSyncExternalStore(subscribePending, getPendingSnapshot, getPendingSnapshot);
  return !!conversationId && ids.has(conversationId);
}

// ---------------------------------------------------------------------------
// Cache helpers
// ---------------------------------------------------------------------------

interface ConversationListData {
  data: ConversationSummary[];
  [key: string]: unknown;
}

function isListData(value: unknown): value is ConversationListData {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as { data?: unknown }).data)
  );
}

function isSummaryOf(value: unknown, conversationId: string): value is ConversationSummary {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { id?: unknown }).id === conversationId &&
    !Array.isArray((value as { data?: unknown }).data)
  );
}

/** Status a cached chronological list holds, or null for a non-list query. */
function listMembership(queryKey: QueryKey): ConversationSummary["status"] | "all" | null {
  if (queryKey[2] !== "list") return null;
  // The list API also accepts "all", which ConversationListParams omits.
  const params = queryKey[3] as { status?: string } | undefined;
  const status = params?.status ?? "active";
  return status === "archived" || status === "all" ? status : "active";
}

function affectedQueryKeys(client: QueryClient, butlerName: string, conversationId: string) {
  const keys = client
    .getQueryCache()
    .findAll({ queryKey: conversationKeys.all(butlerName) })
    .map((query) => query.queryKey);
  return [...keys, conversationKeys.byId(conversationId)];
}

/** First cached copy of a conversation in any conversation query. */
export function findCachedConversation(
  client: QueryClient,
  conversationId: string,
): ConversationSummary | undefined {
  for (const query of client.getQueryCache().findAll({ queryKey: ["conversations"] })) {
    const data = query.state.data;
    if (isListData(data)) {
      const row = data.data.find((conversation) => conversation.id === conversationId);
      if (row) return row;
    } else if (isSummaryOf(data, conversationId)) {
      return data;
    }
  }
  return undefined;
}

function insertChronologically(rows: ConversationSummary[], row: ConversationSummary) {
  const at = rows.findIndex((other) => other.updated_at < row.updated_at);
  return at < 0 ? [...rows, row] : [...rows.slice(0, at), row, ...rows.slice(at)];
}

/**
 * Write one conversation into every cached view of its butler: lists gain or
 * lose it by status, search results and identity lookups update in place.
 * `reorder` re-places a confirmed row by its server `updated_at`.
 */
function placeConversation(client: QueryClient, row: ConversationSummary, reorder: boolean) {
  for (const queryKey of affectedQueryKeys(client, row.butler_name, row.id)) {
    const membership = listMembership(queryKey);
    client.setQueryData<unknown>(queryKey, (current: unknown) => {
      if (isListData(current)) {
        const index = current.data.findIndex((conversation) => conversation.id === row.id);
        if (membership === null) {
          if (index < 0) return current;
          const data = [...current.data];
          data[index] = row;
          return { ...current, data };
        }
        const belongs = membership === "all" || membership === row.status;
        if (!belongs) {
          return index < 0
            ? current
            : { ...current, data: current.data.filter((conversation) => conversation.id !== row.id) };
        }
        if (index >= 0 && !reorder) {
          const data = [...current.data];
          data[index] = row;
          return { ...current, data };
        }
        const without = current.data.filter((conversation) => conversation.id !== row.id);
        return { ...current, data: insertChronologically(without, row) };
      }
      if (current === undefined || isSummaryOf(current, row.id)) {
        // Seed or update the identity lookup so a selected conversation that
        // leaves the Active list keeps rendering in the shared header.
        return queryKey[1] === "by-id" || current !== undefined ? row : current;
      }
      return current;
    });
  }
}

interface CachedSlot {
  queryKey: QueryKey;
  row: ConversationSummary | undefined;
  index: number;
}

function snapshotConversation(client: QueryClient, confirmed: ConversationSummary): CachedSlot[] {
  return affectedQueryKeys(client, confirmed.butler_name, confirmed.id).map((queryKey) => {
    const data = client.getQueryData<unknown>(queryKey);
    if (isListData(data)) {
      const index = data.data.findIndex((conversation) => conversation.id === confirmed.id);
      return { queryKey, row: index >= 0 ? data.data[index] : undefined, index };
    }
    if (isSummaryOf(data, confirmed.id)) return { queryKey, row: data, index: -1 };
    return { queryKey, row: queryKey[1] === "by-id" ? confirmed : undefined, index: -1 };
  });
}

/** Return only this conversation to its last confirmed value in each view. */
function restoreConversation(client: QueryClient, conversationId: string, slots: CachedSlot[]) {
  for (const slot of slots) {
    client.setQueryData<unknown>(slot.queryKey, (current: unknown) => {
      if (isListData(current)) {
        const without = current.data.filter((conversation) => conversation.id !== conversationId);
        if (!slot.row) return { ...current, data: without };
        const at = Math.min(slot.index, without.length);
        return { ...current, data: [...without.slice(0, at), slot.row, ...without.slice(at)] };
      }
      return slot.row ?? current;
    });
  }
}

// ---------------------------------------------------------------------------
// Mutation runner
// ---------------------------------------------------------------------------

export type MetadataOutcome =
  | { kind: "confirmed"; conversation: ConversationSummary }
  | { kind: "failed"; error: unknown }
  | { kind: "duplicate" };

async function changeConversationMetadata(
  client: QueryClient,
  conversation: ConversationSummary,
  body: ConversationUpdateRequest,
): Promise<MetadataOutcome> {
  const { id, butler_name: butlerName } = conversation;
  if (pendingIds.has(id)) return { kind: "duplicate" };
  setPending(id, true);
  try {
    await Promise.all([
      client.cancelQueries({ queryKey: conversationKeys.all(butlerName) }),
      client.cancelQueries({ queryKey: conversationKeys.byId(id) }),
    ]);
    // Base the optimistic row on the freshest cached copy so an undo or
    // status change never resurrects a stale title.
    const confirmed = findCachedConversation(client, id) ?? conversation;
    const slots = snapshotConversation(client, confirmed);
    placeConversation(client, { ...confirmed, ...body }, false);
    try {
      const updated = await updateConversation(butlerName, id, body);
      placeConversation(client, updated, true);
      return { kind: "confirmed", conversation: updated };
    } catch (error) {
      restoreConversation(client, id, slots);
      return { kind: "failed", error };
    }
  } finally {
    setPending(id, false);
    void client.invalidateQueries({ queryKey: conversationKeys.all(butlerName) });
    void client.invalidateQueries({ queryKey: conversationKeys.byId(id) });
  }
}

// ---------------------------------------------------------------------------
// Public actions
// ---------------------------------------------------------------------------

/** A rollback is not proof of rejection: the outcome stays honest. */
const NOT_CONFIRMED =
  "The change was not confirmed. Showing the last confirmed state while it refreshes.";

export interface RenameOptions {
  /** Called with the submitted title when the rename is not confirmed. */
  onFailed?: (title: string) => void;
}

export interface ConversationMetadataActions {
  rename: (
    conversation: ConversationSummary,
    title: string,
    options?: RenameOptions,
  ) => Promise<MetadataOutcome>;
  archive: (conversation: ConversationSummary) => Promise<MetadataOutcome>;
  unarchive: (conversation: ConversationSummary) => Promise<MetadataOutcome>;
}

export function useConversationMetadata(): ConversationMetadataActions {
  const client = useQueryClient();
  return useMemo(() => {
    async function rename(
      conversation: ConversationSummary,
      title: string,
      options?: RenameOptions,
    ) {
      const outcome = await changeConversationMetadata(client, conversation, { title });
      if (outcome.kind === "failed") {
        toast.error(`Couldn't rename "${conversationTitle(conversation)}"`, {
          description: NOT_CONFIRMED,
        });
        options?.onFailed?.(title);
      }
      return outcome;
    }

    async function undoArchive(conversation: ConversationSummary) {
      const outcome = await changeConversationMetadata(client, conversation, { status: "active" });
      const title = conversationTitle(conversation);
      if (outcome.kind === "confirmed") {
        toast.success(`Restored "${conversationTitle(outcome.conversation)}" to active conversations`);
      } else if (outcome.kind === "failed") {
        toast.error(`Undo not confirmed. "${title}" is still archived.`, {
          description: NOT_CONFIRMED,
          action: { label: "Retry undo", onClick: () => void undoArchive(conversation) },
        });
      }
      return outcome;
    }

    async function archive(conversation: ConversationSummary) {
      const outcome = await changeConversationMetadata(client, conversation, { status: "archived" });
      const title = conversationTitle(conversation);
      if (outcome.kind === "confirmed") {
        toast(`Archived "${title}"`, {
          description: "Find it under Archived.",
          duration: 10_000,
          action: { label: "Undo", onClick: () => void undoArchive(outcome.conversation) },
        });
      } else if (outcome.kind === "failed") {
        toast.error(`Couldn't archive "${title}"`, {
          description: NOT_CONFIRMED,
          action: { label: "Retry", onClick: () => void archive(conversation) },
        });
      }
      return outcome;
    }

    async function unarchive(conversation: ConversationSummary) {
      const outcome = await changeConversationMetadata(client, conversation, { status: "active" });
      const title = conversationTitle(conversation);
      if (outcome.kind === "confirmed") {
        toast.success(`Unarchived "${title}"`);
      } else if (outcome.kind === "failed") {
        toast.error(`Couldn't unarchive "${title}"`, {
          description: NOT_CONFIRMED,
          action: { label: "Retry", onClick: () => void unarchive(conversation) },
        });
      }
      return outcome;
    }

    return { rename, archive, unarchive };
  }, [client]);
}

// ---------------------------------------------------------------------------
// Selection helpers
// ---------------------------------------------------------------------------

/** The more recently updated of two copies of the same conversation. */
export function freshestConversation(
  a: ConversationSummary | null | undefined,
  b: ConversationSummary | null | undefined,
): ConversationSummary | null {
  if (!a) return b ?? null;
  if (!b) return a;
  return b.updated_at > a.updated_at ? b : a;
}

/**
 * The selected conversation's summary, even after it leaves the Active list
 * (archived) or was explicitly opened from Archived history or search. Uses
 * the listed row when present and otherwise the identity lookup, seeded from
 * any cached copy before falling back to a fetch.
 */
export function useSelectedConversation(
  conversationId: string | null,
  rows: ConversationSummary[],
): ConversationSummary | null {
  const client = useQueryClient();
  const listed = conversationId
    ? (rows.find((conversation) => conversation.id === conversationId) ?? null)
    : null;
  const { data } = useQuery({
    queryKey: conversationKeys.byId(conversationId ?? ""),
    queryFn: () => getConversationById(conversationId!),
    initialData: () => (conversationId ? findCachedConversation(client, conversationId) : undefined),
    enabled: !!conversationId && !listed,
    retry: false,
    staleTime: 10_000,
  });
  const detail = data && data.id === conversationId ? data : null;
  return freshestConversation(listed, detail);
}

/**
 * The conversation a surface may choose automatically on open or remount:
 * the most recent active one. Archived rows are never auto-selected, even
 * when a cached list happens to contain them.
 */
export function firstResumableConversation(
  rows: ConversationSummary[],
): ConversationSummary | undefined {
  return rows.find((conversation) => conversation.status !== "archived");
}
