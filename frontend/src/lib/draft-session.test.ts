// REQ-dashboard-shell-003/004: controlled scheduling of real session cleanup and public CAS semantics.
import { afterEach, expect, it, vi } from "vitest";
import { DraftSession } from "@/lib/draft-session";
import { browserDrafts, DraftFailure, type DraftRead, type DraftRecord } from "@/lib/draft-store";

afterEach(() => vi.restoreAllMocks());
it("adopts a foreign successor observed during submitted cleanup before another edit can write", async () => {
  let record: DraftRecord | null = null;
  let listener: (() => void) | undefined;
  let releaseCleanup: (() => void) | undefined;
  let enteredCleanup: (() => void) | undefined;
  const entered = new Promise<void>((resolve) => { enteredCleanup = resolve; });
  const release = new Promise<void>((resolve) => { releaseCleanup = resolve; });
  const read = (): DraftRead => ({ record: structuredClone(record), fence: { storeEpoch: 0, writeRevision: record?.writeRevision ?? 0 }, rejected: false });
  vi.spyOn(browserDrafts, "key").mockResolvedValue("controlled-condition");
  vi.spyOn(browserDrafts, "subscribe").mockImplementation((notify) => { listener = notify; return () => {}; });
  vi.spyOn(browserDrafts, "read").mockImplementation(async () => read());
  vi.spyOn(browserDrafts, "write").mockImplementation(async (key, surface, context, fields, revision, baseline, expected) => {
    if (expected.writeRevision !== (record?.writeRevision ?? 0)) throw new DraftFailure("conflict");
    record = { schemaVersion: 1, state: "live", draftKey: key, surfaceId: surface, context, fields, contentRevision: revision, sourceBaseline: baseline, storeEpoch: 0, writeRevision: (record?.writeRevision ?? 0) + 1, updatedAt: Date.now() };
    return read();
  });
  vi.spyOn(browserDrafts, "remove").mockImplementation(async (_key, expected) => {
    enteredCleanup?.(); await release;
    if (!("contentRevision" in expected) || expected.contentRevision !== record?.contentRevision) return false;
    record = null; return true;
  });
  let visible = { name: "", status: "active", diagnosedAt: "", notes: "" };
  const session = new DraftSession("health.condition", ["create", "new"], visible, (fields) => { visible = fields as typeof visible; });
  session.start(); await session.ready;
  visible = { ...visible, name: "Submitted C" }; const token = session.capture(visible); await session.flush();
  const completed = vi.fn(); const cleanup = session.submitted(token, completed); await entered;
  const previous = read().record!;
  record = { ...previous, contentRevision: "foreign-D", writeRevision: previous.writeRevision + 1, fields: { ...previous.fields, name: "Other tab D" } };
  listener?.(); releaseCleanup?.(); await cleanup;
  try {
    expect(read().record?.fields?.name).toBe("Other tab D");
    expect(completed).not.toHaveBeenCalled();
    expect(visible.name === "Other tab D" || session.presentation.conflict).toBe(true);
    if (session.presentation.conflict) {
      session.edit({ ...visible, name: "A local edit" });
      await expect(session.flush()).rejects.toBeInstanceOf(DraftFailure);
      expect(read().record?.fields?.name).toBe("Other tab D");
    } else {
      expect(visible.name).toBe("Other tab D");
    }
  } finally { session.dispose(); }
});

// REQ-dashboard-shell-003/006: acknowledged cleanup from a retired view cannot reset its reopened successor.
it("keeps a reopened view's fields after the retired submission commits its tombstone", async () => {
  let record: DraftRecord | null = null;
  const listeners = new Set<Parameters<typeof browserDrafts.subscribe>[0]>();
  const read = (): DraftRead => ({ record: structuredClone(record), fence: { storeEpoch: 0, writeRevision: record?.writeRevision ?? 0 }, rejected: false });
  vi.spyOn(browserDrafts, "key").mockResolvedValue("reopened-schedule");
  vi.spyOn(browserDrafts, "subscribe").mockImplementation((notify) => { listeners.add(notify); return () => { listeners.delete(notify); }; });
  vi.spyOn(browserDrafts, "read").mockImplementation(async () => read());
  vi.spyOn(browserDrafts, "write").mockImplementation(async (key, surface, context, fields, revision, baseline, expected) => {
    if (expected.writeRevision !== (record?.writeRevision ?? 0)) throw new DraftFailure("conflict");
    record = { schemaVersion: 1, state: "live", draftKey: key, surfaceId: surface, context, fields, contentRevision: revision, sourceBaseline: baseline, storeEpoch: 0, writeRevision: (record?.writeRevision ?? 0) + 1, updatedAt: Date.now() };
    return read();
  });
  vi.spyOn(browserDrafts, "remove").mockImplementation(async (key, expected, reason, surface, context, instance) => {
    if (!("contentRevision" in expected) || expected.contentRevision !== record?.contentRevision) return false;
    record = { schemaVersion: 1, state: "tombstone", draftKey: key, surfaceId: surface, context, storeEpoch: 0, writeRevision: record.writeRevision + 1, updatedAt: Date.now(), deletionReason: reason };
    listeners.forEach((notify) => notify(instance ? { key, contentRevision: expected.contentRevision, instance, fence: read().fence } : undefined));
    return true;
  });
  const empty = { name: "", cron: "", dispatchMode: "prompt", prompt: "", jobName: "", jobArgsRaw: "", complexity: "workhorse" };
  const first = new DraftSession("schedule.form", ["switchboard", "create", "new"], empty, () => {});
  first.start(); await first.ready;
  const values = { ...empty, name: "Accepted schedule", cron: "0 9 * * *", prompt: "An ordinary task" };
  const token = first.capture(values); await first.flush(); first.dispose();
  let visible = empty;
  const reopened = new DraftSession("schedule.form", ["switchboard", "create", "new"], empty, (fields) => { visible = fields as typeof empty; });
  reopened.start(); await reopened.ready;
  const completed = vi.fn();
  try {
    expect(visible).toEqual(values);
    await first.submitted(token, completed);
    await vi.waitFor(() => expect(read().record?.state).toBe("tombstone"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(visible).toEqual(values);
    expect(completed).not.toHaveBeenCalled();
    reopened.focus(); await new Promise((resolve) => setTimeout(resolve, 0));
    expect(visible).toEqual(values);
    // A later explicit edit is a new revision; unchanged render effects cannot resurrect C.
    reopened.edit(visible); await new Promise((resolve) => setTimeout(resolve, 0));
    expect(read().record?.state).toBe("tombstone");
    reopened.edit({ ...visible, name: "A new explicit edit" }); await reopened.flush();
    expect(read().record?.fields?.name).toBe("A new explicit edit");
  } finally { reopened.dispose(); }
});

// REQ-dashboard-shell-003/006: a read started before cleanup cannot consume its acknowledgement.
it("completes a restored submission when an earlier reconciliation reads its submitted tombstone", async () => {
  const empty = { title: "", content: "", tags: "", sourceUrl: "" };
  const values = { ...empty, title: "Restored research", content: "Complete notes" };
  let record: DraftRecord = { schemaVersion: 1, state: "live", draftKey: "restored-research", surfaceId: "health.research", context: ["create", "new"], fields: values, contentRevision: "restored-C", sourceBaseline: "baseline", storeEpoch: 0, writeRevision: 1, updatedAt: Date.now() };
  const read = (): DraftRead => ({ record: structuredClone(record), fence: { storeEpoch: 0, writeRevision: record.writeRevision }, rejected: false });
  let releaseReconciliation!: () => void;
  let releaseConfirmation!: () => void;
  let cleanupEntered!: () => void;
  const reconciliation = new Promise<void>((resolve) => { releaseReconciliation = resolve; });
  const confirmation = new Promise<void>((resolve) => { releaseConfirmation = resolve; });
  const entered = new Promise<void>((resolve) => { cleanupEntered = resolve; });
  let delayedRead = false;
  vi.spyOn(browserDrafts, "key").mockResolvedValue("restored-research");
  vi.spyOn(browserDrafts, "subscribe").mockReturnValue(() => {});
  vi.spyOn(browserDrafts, "read").mockImplementation(async () => {
    if (delayedRead) { delayedRead = false; await reconciliation; }
    else if (record.state === "tombstone") await confirmation;
    return read();
  });
  vi.spyOn(browserDrafts, "remove").mockImplementation(async () => {
    record = { schemaVersion: 1, state: "tombstone", draftKey: "restored-research", surfaceId: "health.research", context: ["create", "new"], storeEpoch: 0, writeRevision: 2, updatedAt: Date.now(), deletionReason: "submitted" };
    cleanupEntered(); return true;
  });
  let visible = empty;
  const session = new DraftSession("health.research", ["create", "new"], empty, (fields) => { visible = fields as typeof empty; });
  session.start(); await session.ready;
  const complete = vi.fn();
  try {
    expect(visible).toEqual(values);
    const token = session.capture(visible);
    delayedRead = true; session.focus();
    const cleanup = session.submitted(token, complete); await entered;
    releaseReconciliation(); await new Promise((resolve) => setTimeout(resolve, 0));
    releaseConfirmation(); await cleanup;
    expect(record.state).toBe("tombstone");
    expect(visible).toEqual(empty);
    expect(complete).toHaveBeenCalledTimes(1);
  } finally { releaseReconciliation(); releaseConfirmation(); session.dispose(); }
});

// REQ-dashboard-shell-006: unchanged queued render effects do not turn cleanup into a successor edit.
it("keeps submission completion eligible across queued old and reset field effects", async () => {
  const empty = { title: "", content: "", tags: "", sourceUrl: "" };
  const values = { ...empty, title: "Restored research", content: "Complete notes" };
  let record: DraftRecord | null = null;
  const read = (): DraftRead => ({ record, fence: { storeEpoch: 0, writeRevision: record?.writeRevision ?? 0 }, rejected: false });
  vi.spyOn(browserDrafts, "key").mockResolvedValue("queued-research");
  vi.spyOn(browserDrafts, "subscribe").mockReturnValue(() => {});
  vi.spyOn(browserDrafts, "read").mockImplementation(async () => read());
  vi.spyOn(browserDrafts, "write").mockImplementation(async (key, surface, context, fields, revision) => {
    record = { schemaVersion: 1, state: "live", draftKey: key, surfaceId: surface, context, fields, contentRevision: revision, sourceBaseline: "baseline", storeEpoch: 0, writeRevision: 1, updatedAt: Date.now() };
    return read();
  });
  vi.spyOn(browserDrafts, "remove").mockImplementation(async () => { record = null; return true; });
  let queuedEffects = false;
  const session = new DraftSession("health.research", ["create", "new"], empty, () => {
    if (queuedEffects) { queuedEffects = false; session.edit(values); session.edit(empty); }
  });
  session.start(); await session.ready;
  const complete = vi.fn();
  try {
    const token = session.capture(values); await session.flush(); queuedEffects = true;
    await session.submitted(token, complete);
    expect(complete).toHaveBeenCalledTimes(1);
    expect(record).toBeNull();
  } finally { session.dispose(); }
});
