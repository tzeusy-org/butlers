import { expect, test, type BrowserContext, type Page } from "@playwright/test";

// REQ-dashboard-shell-002, REQ-dashboard-shell-003, REQ-dashboard-shell-004,
// REQ-dashboard-shell-006. Real browser storage; API fixtures provide no draft service.
async function api(context: BrowserContext) {
  await context.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const expires = new Date(Date.now() + 3_600_000).toISOString();
    let data: unknown = [];
    if (pathname.endsWith("/auth/owner/status")) data = { state: "configured_key", authenticated: true, session_expires_at: expires };
    else if (pathname.endsWith("/auth/owner/csrf")) data = { csrf_token: "browser-test-csrf", csrf_expires_at: expires, session_expires_at: expires };
    else if (pathname === "/api/health") data = { status: "ok" };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data, meta: { total: 0, limit: 50, offset: 0 } }) });
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
    await route.fulfill({ json: { data: { id: "created-condition" } } });
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
  await context.route((url) => url.pathname === "/api/health/conditions", (route) => route.fulfill({ json: { data: [condition], meta: { total: 1, has_more: false } } }));
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
    await route.fulfill({ json: { data: { state: "configured_key", authenticated: false, session_expires_at: null } } });
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
