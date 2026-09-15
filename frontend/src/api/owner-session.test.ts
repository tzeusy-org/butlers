import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearOwnerSession, logoutOwner, onOwnerSessionLost, ownerFetch, rememberOwnerCsrf } from "./owner-session";

const fetchMock = vi.fn();
const tuple = () => ({ csrf_token: "independent-synthetic-csrf", csrf_expires_at: new Date(Date.now() + 1_800_000).toISOString() });
beforeEach(() => { clearOwnerSession(); vi.stubGlobal("fetch", fetchMock); fetchMock.mockReset(); });
afterEach(() => vi.unstubAllGlobals());
const response = (data: unknown, status = 200) => new Response(JSON.stringify({ data }), { status });

describe("owner cookie request boundary", () => {
  it("renews CSRF once for concurrent unsafe calls, including multipart and streaming requests", async () => {
    fetchMock.mockImplementation(async (url: string) => response(url.endsWith("/csrf") ? tuple() : {}));
    const form = new FormData(); form.set("file", "synthetic");
    await Promise.all([ownerFetch("/api/upload", { method: "POST", body: form }), ownerFetch("/api/chat", { method: "POST", body: "{}" })]);
    expect(fetchMock.mock.calls.map(c => c[0]).filter(u => u.endsWith("/csrf"))).toHaveLength(1);
    const csrfCall = fetchMock.mock.calls.find(c => c[0].endsWith("/csrf"))![1];
    expect(csrfCall).toMatchObject({ mode: "cors", redirect: "error", cache: "no-store", credentials: "same-origin" });
    for (const [, init] of fetchMock.mock.calls.filter(c => !c[0].endsWith("/csrf"))) {
      expect(new Headers(init.headers).get("X-CSRF-Token")).toBe(tuple().csrf_token);
      expect(new Headers(init.headers).get("Content-Type")).toBeNull();
    }
  });
  it("never sends reusable browser header authority, follows redirects or sends cookies across origins", async () => {
    rememberOwnerCsrf(tuple()); fetchMock.mockResolvedValue(response({}));
    await ownerFetch("/api/safe", { headers: { "X-API-Key": "synthetic-forbidden-key" } });
    expect(new Headers(fetchMock.mock.calls[0][1].headers).has("X-API-Key")).toBe(false);
    await expect(ownerFetch("https://external.invalid/api", { method: "POST" })).rejects.toThrow("same HTTPS origin");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("aborts outstanding streams and notifies cache teardown on 401 without replay", async () => {
    rememberOwnerCsrf(tuple()); const lost = vi.fn(); const stop = onOwnerSessionLost(lost);
    fetchMock.mockResolvedValueOnce(response({})).mockResolvedValueOnce(response({}, 401));
    await ownerFetch("/api/stream"); const streamSignal = fetchMock.mock.calls[0][1].signal;
    await ownerFetch("/api/private", { method: "DELETE" });
    expect(streamSignal.aborted).toBe(true); expect(lost).toHaveBeenCalledOnce(); expect(fetchMock).toHaveBeenCalledTimes(2); stop();
  });
  it("does not send a mutation if CSRF recovery fails, or revive a session from a late token response", async () => {
    fetchMock.mockResolvedValueOnce(response({}, 401));
    await expect(ownerFetch("/api/private", { method: "POST" })).rejects.toThrow("Sign in again");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    let resolve!: (r: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise<Response>(done => { resolve = done; }));
    const pending = ownerFetch("/api/private", { method: "POST" });
    clearOwnerSession(); resolve(response(tuple()));
    await expect(pending).rejects.toThrow("Sign in again");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("recovers an evicted CSRF token on explicit logout retry without replaying the failed action", async () => {
    rememberOwnerCsrf(tuple());
    fetchMock.mockResolvedValueOnce(response({}, 403));
    await expect(logoutOwner()).rejects.toThrow("Sign out could not be confirmed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const fresh = { ...tuple(), csrf_token: "fresh-synthetic-csrf" };
    fetchMock.mockResolvedValueOnce(response(fresh)).mockResolvedValueOnce(response({}));
    await logoutOwner();
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      "/api/auth/owner/session", "/api/auth/owner/csrf", "/api/auth/owner/session",
    ]);
    expect(new Headers(fetchMock.mock.calls[2][1].headers).get("X-CSRF-Token")).toBe(fresh.csrf_token);
  });
  it("does not discard a newer CSRF token when an older mutation is denied", async () => {
    rememberOwnerCsrf(tuple());
    let resolve!: (value: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise<Response>(done => { resolve = done; }));
    const pending = ownerFetch("/api/private", { method: "POST" });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const fresh = { ...tuple(), csrf_token: "newer-synthetic-csrf" };
    rememberOwnerCsrf(fresh);
    resolve(response({}, 403)); await pending;
    fetchMock.mockResolvedValueOnce(response({}));
    await ownerFetch("/api/private", { method: "POST" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get("X-CSRF-Token")).toBe(fresh.csrf_token);
  });

});
