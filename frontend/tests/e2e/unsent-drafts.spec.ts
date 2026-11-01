import { emptyApiJson, emptyBoard, butlerDetail, boardRow, condition } from "./fixtures/empty-api";
import { mockJson, mockResponse, type ResponseOf } from "./fixtures/api-responses";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";

// REQ-dashboard-shell-002, REQ-dashboard-shell-003, REQ-dashboard-shell-004,
// REQ-dashboard-shell-006. Real browser storage; API fixtures provide no draft service.
async function api(context: BrowserContext) {
  await context.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const url = new URL(route.request().url());
    const expires = new Date(Date.now() + 3_600_000).toISOString();
    const body = url.pathname.endsWith("/auth/owner/status")
      ? mockJson("ownerStatus", { data: { state: "configured_key", authenticated: true, session_expires_at: expires } })
      : url.pathname.endsWith("/auth/owner/csrf")
        ? mockJson("ownerCsrf", { data: { csrf_token: "browser-test-csrf", csrf_expires_at: expires, session_expires_at: expires } })
        : emptyApiJson(url, route.request().method());
    if (body === undefined) await route.fallback();
    else await route.fulfill({ status: 200, contentType: "application/json", body });
  });
}
async function form(page: Page) {
  await page.goto("/health/conditions");
  await page.getByRole("button", { name: "Add condition", exact: true }).click();
  return page.getByRole("dialog");
}
async function rows(page: Page) {
  return page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open("butlers-drafts", 1);
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    try {
      return await new Promise<Record<string, unknown>[]>((resolve, reject) => {
        const tx = db.transaction("drafts"); const request = tx.objectStore("drafts").getAll();
        request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
      });
    } finally { db.close(); }
  });
}
async function named(page: Page, name: string) {
  await expect.poll(async () => (await rows(page)).some((row) => row.state === "live" && (row.fields as { name?: string })?.name === name)).toBe(true);
}

test.beforeEach(async ({ context }) => { await api(context); });

test("the enrolled research route restores its complete bundle and clears only after successful submission", async ({ page, context }) => {
  const posts: unknown[] = [];
  await context.route((url) => url.pathname === "/api/health/research", async (route) => {
    if (route.request().method() === "POST") {
      posts.push(route.request().postDataJSON());
      await route.fulfill({ json: mockResponse("createResearch", { id: "research-created", title: "A browser research subject", content: "Complete research notes", tags: ["sleep", "reading"], source_url: "https://example.com/research", condition_id: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }) });
    } else await route.fulfill({ json: mockResponse("getResearch", { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } }) });
  });
  await page.goto("/health/research");
  await page.getByRole("button", { name: "Add research", exact: true }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("Title", { exact: true }).fill("A browser research subject");
  await dialog.getByLabel("Content", { exact: true }).fill("Complete research notes");
  await dialog.getByLabel("Tags (optional)").fill("sleep, reading");
  await dialog.getByLabel("Source URL (optional)").fill("https://example.com/research");
  const fields = { title: "A browser research subject", content: "Complete research notes", tags: "sleep, reading", sourceUrl: "https://example.com/research" };
  await expect.poll(async () => (await rows(page)).find((row) => row.surfaceId === "health.research" && row.state === "live")?.fields).toEqual(fields);
  await page.keyboard.press("Escape");
  await dialog.getByRole("button", { name: "Keep draft and close" }).click();
  await expect(dialog).toBeHidden();
  await page.getByRole("button", { name: "Add research", exact: true }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Draft restored", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("Title", { exact: true })).toHaveValue(fields.title);
  await expect(dialog.getByLabel("Content", { exact: true })).toHaveValue(fields.content);
  await expect(dialog.getByLabel("Tags (optional)")).toHaveValue(fields.tags);
  await expect(dialog.getByLabel("Source URL (optional)")).toHaveValue(fields.sourceUrl);
  await dialog.getByRole("button", { name: "Add research", exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(posts).toEqual([{ title: fields.title, content: fields.content, tags: ["sleep", "reading"], source_url: fields.sourceUrl }]);
  await expect.poll(async () => (await rows(page)).find((row) => row.surfaceId === "health.research")?.state).toBe("tombstone");
});

test("confirmed bundle survives abrupt tab close and restores once without moving focus", async ({ page, context }) => {
  const dialog = await form(page);
  await dialog.getByLabel("Name", { exact: true }).fill("A browser condition");
  await dialog.getByLabel("Notes").fill("Ordinary eligible prose stays local");
  await named(page, "A browser condition");
  const before = (await rows(page))[0];
  expect(Object.keys(before.fields as object).sort()).toEqual(["diagnosedAt", "name", "notes", "status"]);
  await page.close(); // no close handler, unload flush, or fabricated shutdown guarantee
  const reopened = await context.newPage();
  const restored = await form(reopened);
  await expect(restored.getByLabel("Name", { exact: true })).toHaveValue("A browser condition");
  await expect(restored.getByLabel("Notes")).toHaveValue("Ordinary eligible prose stays local");
  await expect(restored.getByRole("status")).toHaveText("Draft restored");
  await restored.getByLabel("Notes").focus();
  await restored.getByLabel("Notes").press("Tab");
  await expect(restored.getByRole("status")).toHaveText("Draft restored");
  expect((await rows(reopened))[0].contentRevision).toBe(before.contentRevision);
});

test("two real tabs adopt untouched state, suspend dirty writes, and require explicit override", async ({ page, context }) => {
  const first = await form(page);
  const other = await context.newPage(); const second = await form(other);
  await first.getByLabel("Name", { exact: true }).fill("First tab"); await named(page, "First tab");
  await expect(second.getByLabel("Name", { exact: true })).toHaveValue("First tab");
  await second.getByLabel("Name", { exact: true }).fill("Other tab"); await named(other, "Other tab");
  await expect(first.getByRole("group", { name: "Draft conflict" })).toBeVisible();
  await first.getByLabel("Name", { exact: true }).fill("Explicit winner");
  expect((await rows(page))[0].fields).toMatchObject({ name: "Other tab" });
  await first.getByRole("button", { name: "Use this tab" }).click(); await named(page, "Explicit winner");
  await expect(second.getByRole("group", { name: "Draft conflict" })).toBeVisible();
  await second.getByRole("button", { name: "Load other draft" }).click();
  await expect(second.getByLabel("Name", { exact: true })).toHaveValue("Explicit winner");
  await second.getByRole("button", { name: "Discard draft", exact: true }).click();
  await expect.poll(async () => (await rows(other))[0].state).toBe("tombstone");
  await expect(first.getByRole("group", { name: "Draft conflict" })).toBeVisible();
  expect((await rows(page))[0]).not.toHaveProperty("fields");
});

test("expired and invalid bundles become content-free tombstones before rendering", async ({ page }) => {
  await form(page); await page.getByLabel("Name", { exact: true }).fill("Expired condition"); await named(page, "Expired condition");
  await page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve) => { const r = indexedDB.open("butlers-drafts", 1); r.onsuccess = () => resolve(r.result); });
    await new Promise<void>((resolve) => { const tx = db.transaction("drafts", "readwrite"); const store = tx.objectStore("drafts"); const r = store.getAll(); r.onsuccess = () => { const row = r.result[0]; row.updatedAt = Date.now() - 86_400_001; row.expiresAt = row.updatedAt + 86_400_000; store.put(row, row.draftKey); }; tx.oncomplete = () => resolve(); }); db.close();
  });
  await page.reload(); await page.getByRole("button", { name: "Add condition", exact: true }).click();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue("");
  expect((await rows(page))[0]).toMatchObject({ state: "tombstone", deletionReason: "expired" });
  await page.getByLabel("Name", { exact: true }).fill("Will be rejected"); await named(page, "Will be rejected");
  await page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve) => { const r = indexedDB.open("butlers-drafts", 1); r.onsuccess = () => resolve(r.result); });
    await new Promise<void>((resolve) => { const tx = db.transaction("drafts", "readwrite"); const store = tx.objectStore("drafts"); const r = store.getAll(); r.onsuccess = () => { const row = r.result[0]; row.fields.extra = "unregistered"; store.put(row, row.draftKey); }; tx.oncomplete = () => resolve(); }); db.close();
  });
  await page.reload(); await page.getByRole("button", { name: "Add condition", exact: true }).click();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue("");
  await expect(page.getByText("The saved draft could not be restored.")).toBeVisible();
  expect((await rows(page))[0]).toMatchObject({ state: "tombstone", deletionReason: "invalid" });
  expect((await rows(page))[0]).not.toHaveProperty("fields");
});

test("all dismissal paths keep editing until confirmed save or explicit discard", async ({ page }) => {
  const dialog = await form(page);
  await dialog.getByLabel("Name", { exact: true }).fill("Keep this draft"); await named(page, "Keep this draft");
  await dialog.press("Escape"); await dialog.getByRole("button", { name: "Keep editing" }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await dialog.getByRole("button", { name: "Keep draft and close" }).click(); await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "Add condition", exact: true }).click();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue("Keep this draft");
  await page.getByRole("dialog").press("Escape");
  await page.getByRole("button", { name: "Discard and close" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect((await rows(page))[0].state).toBe("tombstone");
});

test("a real blocked IndexedDB queue produces a finite truthful close choice", async ({ page, context }) => {
  await page.goto("/health/conditions");
  await page.evaluate(async () => {
    const r = indexedDB.open("butlers-drafts", 1);
    r.onupgradeneeded = () => { for (const name of ["drafts", "chatAttempts", "meta"]) r.result.createObjectStore(name); };
    await new Promise<void>((resolve) => { r.onsuccess = () => { (window as unknown as { held: IDBDatabase }).held = r.result; resolve(); }; });
    indexedDB.deleteDatabase("butlers-drafts"); // held connection genuinely blocks the following opens
  });
  const other = await context.newPage(); const dialog = await form(other);
  await dialog.getByLabel("Name", { exact: true }).fill("Still editable");
  await expect(dialog.getByText("Draft saving unavailable. Keep this tab open.")).toBeVisible({ timeout: 2500 });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  const started = Date.now();
  await dialog.getByRole("button", { name: "Retry saving" }).click();
  await expect(dialog.getByRole("button", { name: "Close with save unconfirmed" })).toBeEnabled({ timeout: 2000 });
  expect(Date.now() - started).toBeLessThan(1800);
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue("Still editable");
  await dialog.getByRole("button", { name: "Close with save unconfirmed" }).click(); await expect(dialog).not.toBeVisible();
});

test("late success cannot close a successor or erase byte-identical recreation after epoch compaction", async ({ page, context }) => {
  let release!: () => void; let posts = 0;
  const response = new Promise<void>((resolve) => { release = resolve; });
  await context.route((url) => url.pathname === "/api/health/conditions", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    posts++; await response;
    await route.fulfill({ json: mockResponse("createCondition", condition({ id: "created-condition" })) });
  });
  const dialog = await form(page);
  await dialog.getByLabel("Name", { exact: true }).fill("Identical content"); await named(page, "Identical content");
  const original = (await rows(page))[0];
  await dialog.getByRole("button", { name: "Add condition", exact: true }).click();
  await expect.poll(() => posts).toBe(1);
  await dialog.press("Escape"); await dialog.getByRole("button", { name: "Keep draft and close" }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "Add condition", exact: true }).click();
  await page.getByRole("button", { name: "Discard draft", exact: true }).click();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue("");
  await page.getByLabel("Name", { exact: true }).fill("Identical content"); await named(page, "Identical content");
  const recreated = (await rows(page))[0]; expect(recreated.contentRevision).not.toBe(original.contentRevision);
  await page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve) => { const r = indexedDB.open("butlers-drafts", 1); r.onsuccess = () => resolve(r.result); });
    await new Promise<void>((resolve) => { const tx = db.transaction("drafts", "readwrite"); const store = tx.objectStore("drafts"); const r = store.getAll(); r.onsuccess = () => {
      const row = r.result[0]; const now = Date.now();
      for (let i = 0; i < 256; i++) { const key = `compaction-control-${i}`; store.put({ schemaVersion: 1, state: "tombstone", draftKey: key, surfaceId: row.surfaceId, context: row.context, storeEpoch: row.storeEpoch, writeRevision: 1, updatedAt: now, deletedAt: now, deletionReason: "discarded" }, key); }
    }; tx.oncomplete = () => resolve(); }); db.close(); window.dispatchEvent(new Event("focus"));
  });
  await expect.poll(async () => (await rows(page)).find((row) => row.state === "live")?.storeEpoch).toBe(Number(recreated.storeEpoch) + 1);
  const compacted = (await rows(page)).find((row) => row.state === "live")!;
  expect(compacted).toMatchObject({ contentRevision: recreated.contentRevision, updatedAt: recreated.updatedAt, expiresAt: recreated.expiresAt });
  const completed = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/health/conditions");
  release();
  await (await completed).finished();
  await expect(page.getByText("Condition added.")).not.toBeVisible();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue("Identical content");
  expect((await rows(page)).find((row) => row.state === "live")?.contentRevision).toBe(recreated.contentRevision);
  expect(posts).toBe(1);
});

test("oversize edits remain visible without truncating or overwriting the last committed bundle", async ({ page }) => {
  const dialog = await form(page);
  await dialog.getByLabel("Name", { exact: true }).fill("Last valid commit"); await named(page, "Last valid commit");
  const before = (await rows(page))[0];
  await dialog.getByLabel("Notes").fill("界".repeat(23_000));
  await expect(dialog.getByText("Draft saving unavailable. Keep this tab open.")).toBeVisible();
  expect(await dialog.getByLabel("Notes").evaluate((node) => (node as HTMLTextAreaElement).value.length)).toBe(23_000);
  expect((await rows(page))[0].contentRevision).toBe(before.contentRevision);
  await dialog.press("Escape");
  await expect(dialog.getByText("Latest changes may not be recoverable. A browser draft may still appear.")).toBeVisible();
  await dialog.getByRole("button", { name: "Keep editing" }).click();
  await expect(dialog).toBeVisible();
});

test("changed edit source stays authoritative until the owner explicitly loads its draft", async ({ page, context }) => {
  const condition = { id: "stable-condition", name: "Server original", status: "active", notes: "Current server notes", diagnosed_at: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
  await context.route((url) => url.pathname === "/api/health/conditions", (route) => route.fulfill({ json: mockResponse("getConditions", { data: [condition], meta: { total: 1, offset: 0, limit: 50, has_more: false } }) }));
  await page.goto("/health/conditions"); await page.getByRole("button", { name: "Edit Server original", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Unsubmitted edit"); await named(page, "Unsubmitted edit");
  condition.name = "Server changed";
  condition.updated_at = new Date(Date.now() + 1000).toISOString();
  await page.reload(); await page.getByRole("button", { name: "Edit Server changed", exact: true }).click();
  await expect(page.getByText("The source changed since this draft was saved.")).toBeVisible();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue("Server changed");
  await page.getByRole("button", { name: "Load draft", exact: true }).click();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue("Unsubmitted edit");
});


test("chat restores local text without replay and preserves the existing accepted message identity", async ({ page, context }) => {
  const posts: { message: string; message_id: string }[] = [];
  await context.route((url) => /\/butlers\/[^/]+\/conversations$/.test(url.pathname), async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    posts.push(route.request().postDataJSON());
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: done\ndata: {}\n\n" });
  });
  await page.goto("/chat");
  const chat = page.getByTestId("chat-page");
  const input = chat.getByRole("textbox", { name: "Type a message...", exact: true });
  await input.fill("A locally recoverable chat message");
  await expect.poll(async () => (await rows(page)).some((row) => (row.fields as { message?: string })?.message === "A locally recoverable chat message")).toBe(true);
  const revision = (await rows(page))[0].contentRevision;
  await page.reload();
  await expect(input).toHaveValue("A locally recoverable chat message");
  expect(posts).toHaveLength(0);
  expect((await rows(page))[0].contentRevision).toBe(revision);
  await expect(page.getByTestId("chat-dock").getByRole("textbox", { name: "Type a message...", exact: true })).toHaveValue("A locally recoverable chat message");
  await chat.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ message: "A locally recoverable chat message", message_id: expect.any(String) });
  expect(posts[0].message_id).not.toBe(revision);
  await expect.poll(async () => (await rows(page))[0].state).toBe("tombstone");
  await expect(input).toHaveValue("");
  await page.reload();
  await expect(input).toHaveValue("");
  expect(posts).toHaveLength(1);
});

test("native quota refusal preserves editable values and does not prevent one valid domain submission", async ({ page, context }) => {
  const cdp = await context.newCDPSession(page);
  await cdp.send("Storage.overrideQuotaForOrigin", { origin: new URL(test.info().project.use.baseURL!).origin, quotaSize: 1 });
  let posts = 0;
  await context.route((url) => url.pathname === "/api/health/conditions", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    posts++;
    await route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "Please retry later" } } });
  });
  const dialog = await form(page);
  await dialog.getByLabel("Name", { exact: true }).fill("Quota cannot discard this input");
  await expect(dialog.getByText("Draft saving unavailable. Keep this tab open.")).toBeVisible();
  await dialog.getByRole("button", { name: "Add condition", exact: true }).click();
  await expect.poll(() => posts).toBe(1);
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue("Quota cannot discard this input");
  expect((await rows(page)).filter((row) => row.state === "live")).toHaveLength(0);
});

test("a genuinely queued transaction times out without a late write or false close", async ({ page }) => {
  const dialog = await form(page);
  await page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve) => { const r = indexedDB.open("butlers-drafts", 1); r.onsuccess = () => resolve(r.result); });
    const tx = db.transaction("drafts", "readwrite");
    const store = tx.objectStore("drafts");
    const deadline = Date.now() + 3000;
    const occupy = () => { const r = store.get("unrelated-lock"); r.onsuccess = () => { if (Date.now() < deadline) occupy(); }; };
    occupy();
    tx.oncomplete = () => db.close();
    tx.onabort = () => db.close();
  });
  await dialog.getByLabel("Name", { exact: true }).fill("An edit behind an owned storage lock");
  await expect(dialog.getByText("Draft saving unavailable. Keep this tab open.")).toBeVisible({ timeout: 2500 });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue("An edit behind an owned storage lock");
  await expect.poll(async () => (await rows(page)).filter((row) => row.state === "live").length).toBe(0);
  await dialog.getByRole("button", { name: "Retry saving", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await named(page, "An edit behind an owned storage lock");
});

test("actual owner-session loss retires stored content and advances the stale-writer epoch", async ({ page, context }) => {
  let expired = false;
  await context.route((url) => url.pathname.endsWith("/auth/owner/status"), async (route) => {
    if (!expired) return route.fallback();
    await route.fulfill({ json: mockResponse("ownerStatus", { data: { state: "configured_key", authenticated: false, session_expires_at: null } }) });
  });
  await context.route((url) => url.pathname === "/api/health/conditions", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    expired = true;
    await route.fulfill({ status: 401, json: { error: { code: "unauthorized", message: "Session ended" } } });
  });
  const dialog = await form(page);
  await dialog.getByLabel("Name", { exact: true }).fill("Retire this committed draft"); await named(page, "Retire this committed draft");
  const before = (await rows(page))[0];
  await dialog.getByRole("button", { name: "Add condition", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect.poll(async () => (await rows(page))[0]?.deletionReason).toBe("auth_reset");
  const after = (await rows(page))[0];
  expect(after.storeEpoch as number).toBeGreaterThan(before.storeEpoch as number);
  expect(after).not.toHaveProperty("fields");
});

// REQ-dashboard-chat-ui-004: empty or whitespace text is an ordered deletion.
test("whitespace-only chat edits tombstone committed text and never restore an affordance", async ({ page, context }) => {
  await context.route((url) => url.pathname === "/api/butlers/switchboard", (route) => route.fulfill({ json: mockResponse("getButler", { meta: {}, data: { name: "switchboard", status: "running", port: 0, type: "butler", sessions_24h: 0, modules: [], schedules: [], skills: [] } }) }));
  for (const posture of ["page", "dock", "floating", "panel"] as const) {
    const open = async () => {
      await page.setViewportSize({ width: posture === "floating" || posture === "panel" ? 1100 : 1440, height: 900 });
      await page.goto(posture === "panel" ? "/butlers/switchboard?tab=system&section=state" : "/chat");
      if (posture === "floating") await page.getByRole("button", { name: "Talk to Butlers", exact: true }).click();
      if (posture === "panel") await page.getByRole("button", { name: "Chat", exact: true }).click();
      const surface = posture === "page" ? page.getByTestId("chat-page") : posture === "dock" ? page.getByTestId("chat-dock") : posture === "floating" ? page.getByTestId("floating-chat-panel") : page.getByRole("dialog", { name: "Chat with switchboard", exact: true });
      return { surface, input: surface.getByRole("textbox", { name: "Type a message...", exact: true }) };
    };
    const { input } = await open();
    await input.fill(" \t ");
    await expect.poll(async () => (await rows(page))[0]?.state).toBe("tombstone");
    const message = `  Previously committed ${posture} text  `;
    await input.fill(message);
    await expect.poll(async () => (await rows(page))[0]?.fields).toEqual({ message });
    const before = (await rows(page))[0];
    await input.fill(" \n\t ");
    await expect.poll(async () => (await rows(page))[0].state).toBe("tombstone");
    const after = (await rows(page))[0];
    expect(after.deletionReason).toBe("empty");
    expect(after.writeRevision as number).toBeGreaterThan(before.writeRevision as number);
    expect(after).not.toHaveProperty("fields");
    const reopened = await open();
    await expect(reopened.input).toHaveValue("");
    await expect(reopened.surface.getByText("Draft restored", { exact: true })).toHaveCount(0);
  }
});

// REQ-dashboard-shell-004: explicit replacement is a new identity, even for identical bytes.
test("explicit override creates a successor revision protected from an older successful submit", async ({ page, context }) => {
  let accept: (() => void) | undefined;
  let started = false;
  await context.route((url) => url.pathname === "/api/health/conditions", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    started = true;
    await new Promise<void>((resolve) => { accept = resolve; });
    await route.fulfill({ json: mockResponse("createCondition", condition({ id: "accepted-condition", name: "Submitted content", status: "active" })) });
  });
  const first = await form(page);
  await first.getByLabel("Name", { exact: true }).fill("Submitted content"); await named(page, "Submitted content");
  const original = (await rows(page))[0].contentRevision;
  const secondPage = await context.newPage(); const second = await form(secondPage);
  await expect(second.getByLabel("Name", { exact: true })).toHaveValue("Submitted content");
  await first.getByRole("button", { name: "Add condition", exact: true }).click();
  await expect.poll(() => started).toBe(true);
  await second.getByLabel("Name", { exact: true }).fill("Other tab content"); await named(secondPage, "Other tab content");
  await expect(first.getByRole("group", { name: "Draft conflict" })).toBeVisible();
  await first.getByRole("button", { name: "Use this tab", exact: true }).click();
  await named(page, "Submitted content");
  const successor = (await rows(page))[0].contentRevision;
  try {
    expect(successor).not.toBe(original);
  } finally { accept?.(); }
  await expect(first.getByRole("button", { name: "Add condition", exact: true })).toBeEnabled();
  await expect(first).toBeVisible();
  await expect(first.getByLabel("Name", { exact: true })).toHaveValue("Submitted content");
  expect((await rows(page))[0]).toMatchObject({ state: "live", contentRevision: successor });
});

// REQ-dashboard-chat-ui-004: legacy whitespace rows retire before exposure.
test("a legacy whitespace chat record becomes an ordered tombstone before restore", async ({ page }) => {
  await page.goto("/chat");
  const input = page.getByTestId("chat-page").getByRole("textbox", { name: "Type a message...", exact: true });
  await input.fill("Seed an old valid record");
  await expect.poll(async () => (await rows(page))[0]?.state).toBe("live");
  const before = (await rows(page))[0];
  await page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve) => { const r = indexedDB.open("butlers-drafts", 1); r.onsuccess = () => resolve(r.result); });
    await new Promise<void>((resolve) => { const tx = db.transaction("drafts", "readwrite"); const store = tx.objectStore("drafts"); const r = store.getAll(); r.onsuccess = () => { const old = r.result[0]; old.fields.message = " \n\t "; store.put(old, old.draftKey); }; tx.oncomplete = () => resolve(); });
    db.close();
  });
  await page.reload();
  await expect(input).toHaveValue("");
  await expect.poll(async () => (await rows(page))[0].state).toBe("tombstone");
  const after = (await rows(page))[0];
  expect(after.deletionReason).toBe("empty");
  expect(after.writeRevision as number).toBeGreaterThan(before.writeRevision as number);
  expect(after).not.toHaveProperty("fields");
  await expect(page.getByText("Draft restored", { exact: true })).toHaveCount(0);
});

// REQ-dashboard-shell-004: empty overrides fence stale ordinary writes.
test("explicit whitespace override fences another tab until its deliberate new-content choice", async ({ page, context }) => {
  await page.goto("/chat");
  const first = page.getByTestId("chat-page"); const input = first.getByRole("textbox", { name: "Type a message...", exact: true });
  await input.fill("First text");
  await expect.poll(async () => (await rows(page))[0]?.fields).toEqual({ message: "First text" });
  const other = await context.newPage(); await other.goto("/chat");
  const second = other.getByTestId("chat-page"); const otherInput = second.getByRole("textbox", { name: "Type a message...", exact: true });
  await expect(otherInput).toHaveValue("First text");
  await otherInput.fill("Other text");
  await expect.poll(async () => (await rows(other))[0]?.fields).toEqual({ message: "Other text" });
  const otherRevision = (await rows(other))[0].contentRevision;
  await expect(first.getByRole("group", { name: "Draft conflict" })).toBeVisible();
  await input.fill(" \n\t ");
  await first.getByRole("button", { name: "Use this tab", exact: true }).click();
  await expect.poll(async () => (await rows(page))[0].state).toBe("tombstone");
  const tombstone = (await rows(page))[0];
  await expect(second.getByRole("group", { name: "Draft conflict" })).toBeVisible();
  await otherInput.fill("An ordinary stale edit");
  await other.bringToFront();
  await expect(second.getByRole("group", { name: "Draft conflict" })).toBeVisible();
  expect((await rows(other))[0]).toMatchObject({ state: "tombstone", writeRevision: tombstone.writeRevision });
  await second.getByRole("button", { name: "Use this tab", exact: true }).click();
  await expect.poll(async () => (await rows(other))[0]?.fields).toEqual({ message: "An ordinary stale edit" });
  expect((await rows(other))[0].contentRevision).not.toBe(otherRevision);
});


// REQ-dashboard-shell-003/004: cleanup may neither hide nor implicitly overwrite a foreign successor.
test("a successful submit reconciles a queued other-tab successor before the next edit", async ({ page, context }) => {
  let accept: (() => void) | undefined;
  let started = false;
  await context.route((url) => url.pathname === "/api/health/conditions", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    started = true;
    await new Promise<void>((resolve) => { accept = resolve; });
    await route.fulfill({ json: mockResponse("createCondition", condition({ id: "accepted-condition", name: "Submitted C", status: "active" })) });
  });
  const first = await form(page);
  await first.getByLabel("Name", { exact: true }).fill("Submitted C"); await named(page, "Submitted C");
  const other = await context.newPage(); const second = await form(other);
  await expect(second.getByLabel("Name", { exact: true })).toHaveValue("Submitted C");
  await first.getByRole("button", { name: "Add condition", exact: true }).click();
  await expect.poll(() => started).toBe(true);
  // The second tab commits through native IndexedDB while submitted-C cleanup queues behind it.
  // Holding the transaction, rather than sleeping the test, fixes the ordering of both real transactions.
  await other.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve) => { const r = indexedDB.open("butlers-drafts", 1); r.onsuccess = () => resolve(r.result); });
    const tx = db.transaction("drafts", "readwrite"); const store = tx.objectStore("drafts");
    const until = Date.now() + 600;
    const request = store.getAll(); request.onsuccess = () => {
      const record = request.result[0]; record.fields.name = "Other tab D";
      record.contentRevision = crypto.randomUUID(); record.writeRevision += 1;
      store.put(record, record.draftKey);
      const occupy = () => { const r = store.get("queue-lock"); r.onsuccess = () => { if (Date.now() < until) occupy(); }; }; occupy();
    };
    tx.oncomplete = () => { db.close(); const channel = new BroadcastChannel("butlers-drafts-v1"); channel.postMessage({ changed: true }); channel.close(); };
  });
  accept?.();
  await expect(first.getByLabel("Name", { exact: true })).toHaveValue("");
  await named(other, "Other tab D");
  await expect(first).toBeVisible();
  await expect(first.getByRole("button", { name: "Add condition", exact: true })).toBeEnabled();
  // Either explicit conflict or adoption must precede writing against D's fence.
  const input = first.getByLabel("Name", { exact: true });
  await expect.poll(async () => await input.inputValue() === "Other tab D" || await first.getByRole("group", { name: "Draft conflict" }).isVisible()).toBe(true);
  if (await first.getByRole("group", { name: "Draft conflict" }).isVisible()) {
    await input.fill("A later local edit");
    await expect(first.getByRole("group", { name: "Draft conflict" })).toBeVisible();
    expect((await rows(page))[0].fields).toMatchObject({ name: "Other tab D" });
    await first.getByRole("button", { name: "Load other draft", exact: true }).click();
    await expect(input).toHaveValue("Other tab D");
  }
});

// REQ-dashboard-shell-002/003: reopening a mounted create view cannot turn unsent input into its baseline.
test("a kept state-create draft remains dirty on reopen and clearing the full bundle tombstones it", async ({ page, context }) => {
  await context.route((url) => url.pathname === "/api/butlers/switchboard", (route) => route.fulfill({ json: mockResponse("getButler", { meta: {}, data: butlerDetail("switchboard") }) }));
  await page.goto("/butlers/switchboard?tab=system&section=state");
  await page.getByRole("button", { name: "Set value", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Set value", exact: true });
  await dialog.getByLabel("Key", { exact: true }).fill("draft.key");
  await dialog.getByLabel("Value (JSON)", { exact: true }).fill('"local value"');
  await expect.poll(async () => (await rows(page))[0]?.fields).toEqual({ key: "draft.key", value: '"local value"' });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await dialog.getByRole("button", { name: "Keep draft and close", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "Set value", exact: true }).click();
  await expect(dialog.getByLabel("Key", { exact: true })).toHaveValue("draft.key");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog.getByRole("group", { name: "Unsaved draft" })).toBeVisible();
  await dialog.getByRole("button", { name: "Keep editing", exact: true }).click();
  await dialog.getByLabel("Key", { exact: true }).fill("");
  await dialog.getByLabel("Value (JSON)", { exact: true }).fill("");
  await expect.poll(async () => (await rows(page))[0]?.state).toBe("tombstone");
  expect((await rows(page))[0]).not.toHaveProperty("fields");
});


// REQ-dashboard-shell-006: an old failure belongs to its retired view, not a reopened successor.
test("a late state-save failure cannot announce into the reopened state form", async ({ page, context }) => {
  await context.route((url) => url.pathname === "/api/butlers/switchboard", (route) => route.fulfill({ json: mockResponse("getButler", { meta: {}, data: butlerDetail("switchboard") }) }));
  let rejectSave: (() => void) | undefined; let started = false;
  await context.route((url) => url.pathname.startsWith("/api/butlers/switchboard/state/"), async (route) => {
    if (route.request().method() === "GET") return route.fallback();
    started = true;
    await new Promise<void>((resolve) => { rejectSave = resolve; });
    await route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "Old request failed" } } });
  });
  await page.goto("/butlers/switchboard?tab=system&section=state");
  await page.getByRole("button", { name: "Set value", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Set value", exact: true });
  await dialog.getByLabel("Key", { exact: true }).fill("pending.key");
  await dialog.getByLabel("Value (JSON)", { exact: true }).fill('"pending value"');
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => started).toBe(true);
  await dialog.press("Escape");
  await dialog.getByRole("button", { name: "Keep draft and close", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "Set value", exact: true }).click();
  await expect(dialog.getByLabel("Key", { exact: true })).toHaveValue("pending.key");
  rejectSave?.();
  await expect(dialog.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
  await expect(dialog.getByText("Could not save the value. Your changes are still here. Try again.", { exact: true })).toHaveCount(0);
});


// REQ-dashboard-shell-006: parent-owned schedule feedback must use the submitted view's lifetime.
test("a late schedule success cannot announce or close a reopened schedule", async ({ page, context }) => {
  await context.route((url) => url.pathname === "/api/butlers/switchboard", (route) => route.fulfill({ json: mockResponse("getButler", { meta: {}, data: butlerDetail("switchboard") }) }));
  let finish: (() => void) | undefined; let started = false;
  await context.route((url) => url.pathname === "/api/butlers/switchboard/schedules", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    started = true; await new Promise<void>((resolve) => { finish = resolve; });
    await route.fulfill({ json: mockResponse("createButlerSchedule", { meta: {}, data: { id: "created-schedule" } }) });
  });
  await page.goto("/butlers/switchboard?tab=system&section=schedules");
  await page.getByRole("button", { name: "Add schedule", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Create Schedule", exact: true });
  await dialog.getByLabel("Name", { exact: true }).fill("Old schedule");
  await dialog.getByLabel("Cron Expression", { exact: true }).fill("0 9 * * *");
  await dialog.getByLabel("Prompt", { exact: true }).fill("An ordinary scheduled task");
  await dialog.getByRole("button", { name: "Create Schedule", exact: true }).click();
  await expect.poll(() => started).toBe(true);
  await dialog.press("Escape");
  await dialog.getByRole("button", { name: "Keep draft and close", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "Add schedule", exact: true }).click();
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue("Old schedule");
  const accepted = page.waitForResponse((response) => response.url().endsWith("/api/butlers/switchboard/schedules") && response.request().method() === "POST" && response.status() === 200);
  finish?.();
  await accepted;
  await expect.poll(async () => (await rows(page)).some((row) => row.surfaceId === "schedule.form" && row.state === "tombstone" && row.deletionReason === "submitted")).toBe(true);
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue("Old schedule");
  await expect(dialog.getByLabel("Cron Expression", { exact: true })).toHaveValue("0 9 * * *");
  await expect(dialog.getByLabel("Prompt", { exact: true })).toHaveValue("An ordinary scheduled task");
  // Follow acknowledged cleanup with a real edit: it must retain the complete successor bundle.
  await dialog.getByLabel("Name", { exact: true }).fill("Reopened schedule");
  await named(page, "Reopened schedule");
  await expect(dialog.getByRole("button", { name: "Create Schedule", exact: true })).toBeEnabled();
  await expect(page.getByText('Schedule "Old schedule" created', { exact: true })).toHaveCount(0);
  await expect(dialog).toBeVisible();
});


// REQ-dashboard-shell-006: the calendar parent must not announce retired dialog requests.
test("late butler-calendar outcomes leave a reopened event draft alone", async ({ page, context }) => {
  await context.route((url) => url.pathname === "/api/calendar/workspace/invitations", (route) => route.fulfill({ json: mockResponse("getCalendarInvitations", { meta: {}, data: { entries: [], issues_available: true, conflicts_available: true, sources_degraded: [], has_more: false, next_cursor: null } }) }));
  await context.route((url) => url.pathname === "/api/calendar/workspace/conflicts", (route) => route.fulfill({ json: mockResponse("getCalendarWorkspaceConflicts", { meta: {}, data: { issues: [], issues_available: true, scan_window: { start: "2026-01-01T00:00:00Z", end: "2026-01-02T00:00:00Z" } } }) }));
  const lane = { lane_id: "general", butler_name: "general", title: "General lane", source_keys: ["internal_reminders:general"] };
  await context.route((url) => url.pathname === "/api/calendar/workspace/meta", (route) => route.fulfill({ json: mockResponse("getCalendarWorkspaceMeta", { meta: {}, data: { capabilities: { views: ["user", "butler"], filters: {}, sync: { global: true, by_source: true } }, connected_sources: [], writable_calendars: [], lane_definitions: [lane], default_timezone: "UTC", primary_calendar_id: null } }) }));
  await context.route((url) => url.pathname === "/api/calendar/workspace", (route) => route.fulfill({ json: mockResponse("getCalendarWorkspace", { meta: {}, data: { entries: [], source_freshness: [], lanes: [lane], next_cursor: null, has_more: false } }) }));
  let release: (() => void) | undefined; let started = false;
  await context.route((url) => url.pathname === "/api/calendar/workspace/butler-events", async (route) => {
    started = true; await new Promise<void>((resolve) => { release = resolve; });
    await route.fulfill({ json: mockResponse("mutateCalendarWorkspaceButlerEvent", { meta: {}, data: { action: "create", tool_name: "calendar_create_event", request_id: null, result: { status: "ok" }, conflicts: [], suggested_slots: [], projection_version: null, staleness_ms: null, projection_freshness: null } }) });
  });
  await page.goto("/calendar?view=butler");
  await page.getByRole("button", { name: "Create butler event", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Create butler event", exact: true });
  await dialog.getByLabel("Title", { exact: true }).fill("Old calendar draft");
  await dialog.getByRole("button", { name: "Create event", exact: true }).click();
  await expect.poll(() => started).toBe(true);
  await dialog.press("Escape");
  await dialog.getByRole("button", { name: "Keep draft and close", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "Create butler event", exact: true }).click();
  await expect(dialog.getByLabel("Title", { exact: true })).toHaveValue("Old calendar draft");
  release?.();
  await expect(dialog.getByRole("button", { name: "Create event", exact: true })).toBeEnabled();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Title", { exact: true })).toHaveValue("Old calendar draft");
  await expect(page.getByText("Butler event created", { exact: true })).toHaveCount(0);
});


// REQ-dashboard-chat-ui-004: an explicit existing Retry retains the exact submitted C binding.
test("accepted Retry clears the failed submission's C and preserves a newer composer D", async ({ page, context }) => {
  const posts: { message: string; message_id: string }[] = [];
  let failNext = true;
  await context.route((url) => /\/butlers\/[^/]+\/conversations$/.test(url.pathname), async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    posts.push(route.request().postDataJSON());
    if (failNext) { failNext = false; await route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "Please retry" } } }); }
    else await route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: done\ndata: {}\n\n" });
  });
  await page.goto("/chat");
  const chat = page.getByTestId("chat-page"); const input = chat.getByRole("textbox", { name: "Type a message...", exact: true });
  for (const newer of [false, true]) {
    failNext = true;
    await input.fill(newer ? "Second failed submission C" : "First failed submission C");
    await expect.poll(async () => (await rows(page))[0]?.state).toBe("live");
    const submitted = (await rows(page))[0].contentRevision;
    await chat.getByRole("button", { name: "Send message", exact: true }).click();
    await expect(chat.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
    expect((await rows(page))[0].contentRevision).toBe(submitted);
    if (newer) {
      await input.fill("Newer unsent D");
      await expect.poll(async () => (await rows(page))[0]?.fields).toEqual({ message: "Newer unsent D" });
    }
    const successor = (await rows(page))[0].contentRevision;
    const response = page.waitForResponse((r) => r.request().method() === "POST" && /\/conversations$/.test(new URL(r.url()).pathname));
    await chat.getByRole("button", { name: "Retry", exact: true }).click(); await (await response).finished();
    await expect(chat.getByRole("button", { name: "Retry", exact: true })).toHaveCount(0);
    expect(posts.at(-1)?.message_id).toBe(posts.at(-2)?.message_id);
    if (newer) {
      expect((await rows(page))[0]).toMatchObject({ state: "live", contentRevision: successor, fields: { message: "Newer unsent D" } });
      await expect(input).toHaveValue("Newer unsent D");
    } else await expect.poll(async () => (await rows(page))[0]?.state).toBe("tombstone");
  }
});

// Owning-butler identity must survive cached sibling navigation (REQ001/006).
async function siblingButlers(context: BrowserContext) {
  await context.route((url) => url.pathname === "/api/butlers/board", async (route) => {
    const board = ["switchboard", "general"].map((name): ResponseOf<"getButlersBoard">["data"]["rows"][number] => ({
      ...boardRow(name), name, type: "butler", status: "running", activity: "idle", cell_tone: "neutral",
      eligibility: "active", sessions_24h: 0, cost_today: 0, active_session_count: 0,
      hourly_stripe: [], hourly_total: 0, heartbeat_unavailable: true, cadence_status: "unknown",
    }));
    await route.fulfill({ json: mockResponse("getButlersBoard", { meta: {}, data: { ...emptyBoard().data, rows: board, aggregates: { ...emptyBoard().data.aggregates, total: 2, butler_count: 2 } } }) });
  });
  await context.route((url) => /^\/api\/butlers\/(general|switchboard)$/.test(url.pathname), async (route) => {
    await route.fulfill({ json: mockResponse("getButler", { meta: {}, data: butlerDetail(new URL(route.request().url()).pathname.split("/")[3]) }) });
  });
}

test("cached butler navigation keeps state input with its owning butler only", async ({ page, context }) => {
  await siblingButlers(context);
  await page.goto("/butlers/general?tab=system&section=state");
  await expect(page.getByRole("button", { name: "Set value", exact: true })).toBeVisible();
  await page.getByRole("navigation", { name: "Navigate to butler" }).getByRole("link", { name: "switchboard" }).click();
  await page.getByRole("tab", { name: "State", exact: true }).click();
  await page.getByRole("button", { name: "Set value", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Set value", exact: true });
  await dialog.getByLabel("Key", { exact: true }).fill("A-only-key");
  await dialog.getByLabel("Value (JSON)").fill('"A-only-value"');
  await expect.poll(async () => (await rows(page)).filter((row) => row.state === "live").length).toBe(1);
  const original = (await rows(page)).find((row) => row.state === "live")!;
  const back = page.goBack();
  await page.getByRole("button", { name: "Keep draft and close", exact: true }).click();
  await back;
  await expect(page).toHaveURL(/butlers\/general\?tab=system&section=state/);
  await expect(dialog).toBeHidden();
  await page.getByRole("button", { name: "Set value", exact: true }).click();
  await expect(dialog.getByLabel("Key", { exact: true })).toHaveValue("");
  await expect(dialog.getByLabel("Value (JSON)")).toHaveValue("");
  expect((await rows(page)).filter((row) => row.state === "live")).toEqual([original]);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.goForward();
  await page.getByRole("button", { name: "Set value", exact: true }).click();
  await expect(dialog.getByLabel("Key", { exact: true })).toHaveValue("A-only-key");
  await expect(dialog.getByLabel("Value (JSON)")).toHaveValue('"A-only-value"');
});

test("cached butler navigation fences old schedule feedback and keeps current success", async ({ page, context }) => {
  await siblingButlers(context);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let started!: () => void;
  const requested = new Promise<void>((resolve) => { started = resolve; });
  await context.route((url) => /^\/api\/butlers\/(switchboard|general)\/schedules$/.test(url.pathname), async (route) => {
    if (route.request().method() !== "POST") { await route.fulfill({ json: mockResponse("getButlerSchedules", { meta: {}, data: [] }) }); return; }
    if (new URL(route.request().url()).pathname.includes("switchboard")) { started(); await held; }
    await route.fulfill({ json: mockResponse("createButlerSchedule", { meta: {}, data: { id: "created", ...route.request().postDataJSON() } }) });
  });
  await page.goto("/butlers/general?tab=system&section=schedules");
  await expect(page.getByRole("button", { name: "Add schedule", exact: true })).toBeVisible();
  await page.getByRole("navigation", { name: "Navigate to butler" }).getByRole("link", { name: "switchboard" }).click();
  await page.getByRole("tab", { name: "Schedules", exact: true }).click();
  const open = async (name: string) => {
    await page.getByRole("button", { name: "Add schedule", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Create Schedule", exact: true });
    await dialog.getByLabel("Name", { exact: true }).fill(name);
    await dialog.getByLabel("Cron Expression").fill("0 * * * *");
    await dialog.getByLabel("Prompt", { exact: true }).fill("Ordinary schedule prompt");
    return dialog;
  };
  const a = await open("A-pending-schedule");
  await a.getByRole("button", { name: "Create Schedule", exact: true }).click();
  await requested;
  const back = page.goBack();
  await page.getByRole("button", { name: "Keep draft and close", exact: true }).click();
  await back;
  await expect(page).toHaveURL(/butlers\/general\?tab=system&section=schedules/);
  await expect(a).toBeHidden();
  release();
  const b = await open("B-current-schedule");
  await b.getByRole("button", { name: "Create Schedule", exact: true }).click();
  await expect(page.getByText('Schedule "B-current-schedule" created', { exact: true })).toBeVisible();
  await expect(page.getByText('Schedule "A-pending-schedule" created', { exact: true })).toBeHidden();
});
