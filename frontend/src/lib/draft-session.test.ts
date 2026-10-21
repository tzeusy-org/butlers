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
