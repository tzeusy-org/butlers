import { test, expect, chromium, type Page, type CDPSession } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { startOwnerAuthHttps } from "../../scripts/owner-auth-https.mjs";

const origin = "https://butlers.example.test";
function host(operation: string, args: string[] = []) {
  const command: unknown = JSON.parse(process.env.OWNER_AUTH_TEST_HOST_COMMAND ?? "null");
  if (!Array.isArray(command) || command.length !== 2 || !command.every(p => typeof p === "string") || !command[1].endsWith("/owner_auth_browser_host.py")) {
    throw new Error("The isolated browser host helper argv is required.");
  }
  try { execFileSync(command[0], [...command.slice(1), operation, ...args], { stdio: "ignore", timeout: 30_000 }); }
  catch { throw new Error("Isolated host operation failed; no command arguments retained."); }
}
async function virtualAuthenticator(page: Page): Promise<{ cdp: CDPSession; authenticatorId: string }> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", { options: {
    protocol: "ctap2", ctap2Version: "ctap2_1", transport: "internal", hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true,
    defaultBackupEligibility: true, defaultBackupState: true,
  } });
  return { cdp, authenticatorId };
}
async function status(page: Page) {
  return page.evaluate(async () => (await (await fetch("/api/auth/owner/status", { cache: "no-store", redirect: "error" })).json()).data);
}
async function approveInBrowser(page: Page, operation: "enroll" | "recover") {
  const command = await page.getByRole("textbox", { name: "Run on your Butlers host" }).inputValue();
  const match = command.match(/--request ([A-Za-z0-9_-]{43})(?: --confirm-revoke)?$/);
  if (!match) throw new Error("Expected one bounded synthetic request ID");
  host(operation === "enroll" ? "authorize-registration" : "authorize-recovery", ["--request", match[1], ...(operation === "recover" ? ["--confirm-revoke"] : [])]);
  await page.getByRole("button", { name: "Check authorization" }).click();
  await page.getByRole("button", { name: "Register passkey", exact: true }).click();
  await expect.poll(async () => (await status(page)).authenticated).toBe(true);
  await expect(page.getByRole("button", { name: "Owner session" })).toBeVisible();
}
async function signOut(page: Page) {
  await page.getByRole("button", { name: "Owner session" }).click();
  await page.getByRole("button", { name: "Sign out this browser" }).click();
  await expect(page.getByRole("button", { name: "Sign in with passkey" })).toBeVisible();
  expect((await status(page)).authenticated).toBe(false);
}

test("real HTTPS native passkey lifecycle, independent CSRF, recovery and private teardown", async () => {
  const target = process.env.OWNER_AUTH_TEST_API_URL;
  if (!target || process.env.OWNER_AUTH_TEST_ISOLATED !== "1") throw new Error("Start the explicitly isolated PostgreSQL/backend harness first.");
  const fixture = await startOwnerAuthHttps(target);
  const browser = await chromium.launch({ args: [`--host-resolver-rules=MAP butlers.example.test 127.0.0.1:${fixture.port}`, "--no-proxy-server"] });
  let phase = "bootstrap";
  try {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    await context.addInitScript(() => {
      if (!navigator.credentials?.create) return;
      const nativeCreate = navigator.credentials.create.bind(navigator.credentials);
      navigator.credentials.create = async options => {
        const credential = await nativeCreate(options);
        if (credential?.type === "public-key") {
          // Match Bitwarden's wrapper without changing the real authenticator proof.
          Object.defineProperty(credential, "getClientExtensionResults", {
            value: () => ({ credProps: undefined }),
          });
        }
        return credential;
      };
    });
    const page = await context.newPage();
    const authenticator = await virtualAuthenticator(page);
    await page.goto(`${origin}/settings?returnTo=https://external.invalid`);
    expect(await page.evaluate(() => ({ origin: location.origin, secure: isSecureContext }))).toEqual({ origin, secure: true });
    await expect(page.getByRole("button", { name: "Register passkey", exact: true })).toBeVisible();
    expect((await status(page)).state).toBe("keyless_unenrolled");
    expect(await page.evaluate(async () => (await fetch("/api/test-private")).status)).toBe(401);
    await page.getByRole("button", { name: "Register passkey", exact: true }).click();
    phase = "registration";
    await approveInBrowser(page, "enroll");
    expect(new URL(page.url()).origin).toBe(origin);
    expect(new URL(page.url()).pathname).toBe("/settings");
    const cookies = (await context.cookies()).filter(c => c.name.endsWith("-owner"));
    expect(cookies.length).toBe(1);
    expect(cookies[0]).toMatchObject({ secure: true, httpOnly: true, sameSite: "Strict", path: "/" });
    expect(await page.evaluate(() => document.cookie.includes("-owner="))).toBe(false);
    phase = "csrf";
    const csrfProof = await page.evaluate(async () => {
      const denied = await fetch("/api/test-private", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const { data } = await (await fetch("/api/auth/owner/csrf", { mode: "cors", cache: "no-store", redirect: "error" })).json();
      const allowed = await fetch("/api/test-private", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": data.csrf_token }, body: "{}" });
      return { denied: denied.status, allowed: allowed.status };
    });
    expect(csrfProof).toEqual({ denied: 403, allowed: 200 });
    phase = "synced-browser";
    // Model provider sync with a second synthetic authenticator. Private test
    // keys stay in memory; no assertion prints the virtual credential record.
    const syncedContext = await browser.newContext({ ignoreHTTPSErrors: true });
    const syncedPage = await syncedContext.newPage();
    const syncedAuthenticator = await virtualAuthenticator(syncedPage);
    const virtualCredentials = await authenticator.cdp.send("WebAuthn.getCredentials", { authenticatorId: authenticator.authenticatorId });
    for (const credential of virtualCredentials.credentials) {
      await syncedAuthenticator.cdp.send("WebAuthn.addCredential", { authenticatorId: syncedAuthenticator.authenticatorId, credential });
    }
    await syncedPage.goto(origin);
    await syncedPage.getByRole("button", { name: "Sign in with passkey" }).click();
    await expect.poll(async () => (await status(syncedPage)).authenticated).toBe(true);
    await syncedContext.close();
    await page.reload();
    await expect(page.getByRole("button", { name: "Owner session" })).toBeVisible();
    phase = "logout-and-cancel";
    await signOut(page);
    await authenticator.cdp.send("WebAuthn.setAutomaticPresenceSimulation", { authenticatorId: authenticator.authenticatorId, enabled: false });
    await page.getByRole("button", { name: "Sign in with passkey" }).click();
    await expect(page.getByText("Waiting for your passkey…")).toBeVisible();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    expect((await status(page)).authenticated).toBe(false);
    await authenticator.cdp.send("WebAuthn.setAutomaticPresenceSimulation", { authenticatorId: authenticator.authenticatorId, enabled: true });
    await page.getByRole("button", { name: "Sign in with passkey" }).click();
    await expect.poll(async () => (await status(page)).authenticated).toBe(true);
    phase = "live-stream-and-query-teardown";
    const evidence = () => page.evaluate(async () => (await fetch("/__test__/evidence")).json());
    await expect.poll(async () => (await evidence()).websockets).toBeGreaterThan(0);
    await expect.poll(async () => (await evidence()).pending_queries).toBeGreaterThan(0);
    await page.evaluate(() => {
      const observation = { chunks: 0, ended: false };
      Object.assign(window, { ownerStreamEvidence: observation });
      void (async () => {
        try {
          const response = await fetch("/api/test-stream");
          const reader = response.body!.getReader();
          while (!(await reader.read()).done) observation.chunks += 1;
        } finally { observation.ended = true; }
      })();
    });
    await expect.poll(async () => (await evidence()).sse).toBe(1);
    await expect.poll(() => page.evaluate(() => (window as unknown as { ownerStreamEvidence: { chunks: number } }).ownerStreamEvidence.chunks)).toBeGreaterThan(0);
    host("expire-sessions");
    // The actual socket close purges the gate and aborts the pending React query
    // without navigation, reload, or a test-triggered HTTP 401.
    await expect(page.getByRole("button", { name: "Sign in with passkey" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Owner session" })).not.toBeVisible();
    await expect.poll(async () => (await evidence()).websockets).toBe(0);
    await expect.poll(async () => (await evidence()).pending_queries).toBe(0);
    await expect.poll(async () => (await evidence()).sse).toBe(0);
    await expect.poll(() => page.evaluate(() => (window as unknown as { ownerStreamEvidence: { ended: boolean } }).ownerStreamEvidence.ended)).toBe(true);
    phase = "expiry-sign-in";
    await page.getByRole("button", { name: "Sign in with passkey" }).click();
    await expect.poll(async () => (await status(page)).authenticated).toBe(true);
    await page.getByRole("button", { name: "Owner session" }).click();
    phase = "revoke-all";
    await page.getByRole("button", { name: "Sign out all browsers" }).click();
    // Wait for the real mutation to complete before navigation can abort it.
    await expect(page.getByRole("button", { name: "Sign in with passkey" })).toBeVisible();
    expect((await status(page)).authenticated).toBe(false);
    phase = "revoke-all-reload";
    await page.reload();
    await expect(page.getByRole("button", { name: "Sign in with passkey" })).toBeVisible();

    phase = "recovery";
    // A fresh browser needs host recovery when the original authenticator is lost.
    const replacement = await browser.newContext({ ignoreHTTPSErrors: true });
    const recoveryPage = await replacement.newPage();
    await virtualAuthenticator(recoveryPage);
    await recoveryPage.goto(origin);
    await recoveryPage.getByRole("button", { name: "Recover access" }).click();
    await approveInBrowser(recoveryPage, "recover");
    expect((await status(recoveryPage)).authenticated).toBe(true);
    expect((await status(page)).authenticated).toBe(false);
    await page.getByRole("button", { name: "Sign in with passkey" }).click();
    await expect(page.getByText("Access could not be verified. Start again.")).toBeVisible();
    expect((await status(page)).authenticated).toBe(false);
    await signOut(recoveryPage);
    await recoveryPage.getByRole("button", { name: "Sign in with passkey" }).click();
    await expect.poll(async () => (await status(recoveryPage)).authenticated).toBe(true);
    // Sensitive material is neither persisted in browser storage nor captured in traces.
    const stored = await recoveryPage.evaluate(async () => {
      localStorage.setItem("synthetic-public-control", "capture-active");
      const keys = [...Object.keys(localStorage), ...Object.keys(sessionStorage)];
      const values = JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]);
      const { data } = await (await fetch("/api/auth/owner/csrf", { mode: "cors" })).json();
      const csrfAbsent = !values.includes(data.csrf_token);
      localStorage.removeItem("synthetic-public-control");
      return { keys, values, csrfAbsent };
    });
    expect(stored.values.includes("capture-active")).toBe(true);
    expect(stored.csrfAbsent).toBe(true);
    expect(stored.keys.some(key => /csrf|passkey|credential|api.?key|owner.?session/i.test(key))).toBe(false);
    const privateValues = [cookies[0].value, ...(await replacement.cookies()).map(c => c.value),
      ...virtualCredentials.credentials.flatMap(c => [c.credentialId, c.privateKey, c.userHandle ?? ""])].filter(Boolean);
    expect(privateValues.some(value => stored.values.includes(value))).toBe(false);
    await replacement.close(); await context.close();
  } catch {
    // Native/CLI/Playwright failures can contain ceremony values or call arguments.
    // Retain only the closed phase label; inspect locally with content-blind probes.
    throw new Error(`Owner authentication browser verification failed during ${phase}.`);
  } finally { await browser.close(); await fixture.close(); }
});


test("configured key browser session and independent automation", async () => {
  const target = process.env.OWNER_AUTH_TEST_API_URL;
  const key = process.env.OWNER_AUTH_TEST_CONFIGURED_KEY;
  if (!target || !key || process.env.OWNER_AUTH_TEST_ISOLATED !== "1") throw new Error("Start the isolated configured-key harness.");
  const fixture = await startOwnerAuthHttps(target);
  const browser = await chromium.launch({ args: [`--host-resolver-rules=MAP butlers.example.test 127.0.0.1:${fixture.port}`, "--no-proxy-server"] });
  try {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage();
    await page.goto(origin);
    expect((await status(page)).state).toBe("configured_key");
    await expect(page.getByRole("button", { name: "Sign in with passkey" })).not.toBeVisible();
    await page.getByLabel("Dashboard API key").fill("wrong-synthetic-key");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByText("Access could not be verified. Start again.")).toBeVisible();
    await expect(page.getByLabel("Dashboard API key")).toHaveValue("");
    await page.getByLabel("Dashboard API key").fill(key);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("button", { name: "Owner session" })).toBeVisible();
    expect((await status(page)).authenticated).toBe(true);
    const automated = await fetch(`${target}/api/test-private`, { headers: { "X-API-Key": key } });
    expect(automated.status).toBe(200);
    const rejected = await page.evaluate(async () => (await fetch("/api/test-private", { headers: { "X-API-Key": "invalid-header" } })).status);
    expect(rejected).toBe(401);
    const stored = await page.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]));
    expect(stored.includes(key)).toBe(false);
    await page.getByRole("button", { name: "Owner session" }).click();
    await page.getByRole("button", { name: "Sign out this browser" }).click();
    await expect(page.getByLabel("Dashboard API key")).toHaveValue("");
    expect((await status(page)).authenticated).toBe(false);
    await context.close();
  } catch {
    throw new Error("Owner authentication browser verification failed during configured-key lifecycle.");
  } finally { await browser.close(); await fixture.close(); }
});
