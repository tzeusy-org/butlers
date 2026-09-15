// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { OwnerGate } from "./OwnerGate";
import { clearOwnerSession, ownerFetch } from "@/api/owner-session";

const future = () => new Date(Date.now() + 300_000).toISOString();
const tuple = () => ({ csrf_token: "synthetic-csrf", csrf_expires_at: future(), session_expires_at: future() });
let authenticated: boolean;
let mode = "keyless_enrolled";
const fetchMock = vi.fn();
const chooser = vi.fn();
const requestId = "A".repeat(43);
const pendingOptions = { state: "pending", expires_at: "2099-01-01T00:00:00Z" };
let approved = false;
function mount() {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  cache.setQueryData(["private"], { sensitive: "synthetic-private-sentinel" });
  render(<QueryClientProvider client={cache}><OwnerGate><p>Protected dashboard</p></OwnerGate></QueryClientProvider>);
  return cache;
}
beforeEach(() => {
  clearOwnerSession(); authenticated = false; mode = "keyless_enrolled"; approved = false;
  vi.stubGlobal("location", new URL("https://butlers.test/return-here?next=https://external.invalid"));
  vi.stubGlobal("isSecureContext", true); vi.stubGlobal("PublicKeyCredential", class {});
  Object.defineProperty(navigator, "credentials", { configurable: true, value: { get: chooser, create: chooser } });
  chooser.mockReset(); fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    const path = url.split("/auth/owner")[1];
    let data: unknown = {};
    if (path === "/status") data = { state: mode, authenticated, session_expires_at: authenticated ? future() : null };
    if (path === "/context" || path === "/csrf") data = tuple();
    if (path === "/registration/intent") data = { request_id: requestId, operation: "enroll", expires_at: future(), canonical_origin: location.origin };
    if (path === "/registration/options") data = approved ? { ceremony_id: requestId, publicKey: {} } : pendingOptions;
    if (path === "/login/options") data = { ceremony_id: requestId, publicKey: { challenge: requestId } };
    return new Response(JSON.stringify({ data }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("owner access user flows", () => {
  it("mounts no protected subtree before verified status, and purges it plus cache on 401", async () => {
    const cache = mount();
    await screen.findByRole("button", { name: "Sign in with passkey" });
    expect(screen.queryByText("Protected dashboard")).toBeNull();
    authenticated = true;
    fireEvent.click(screen.getByRole("button", { name: "Sign in with passkey" }));
    chooser.mockRejectedValueOnce(new DOMException("", "NotAllowedError"));
    // A refresh represents the cookie being received by this browser.
    clearOwnerSession();
    await screen.findByText("Protected dashboard");
    authenticated = false;
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 401 }));
    await ownerFetch("/api/private");
    await waitFor(() => expect(screen.queryByText("Protected dashboard")).toBeNull());
    expect(cache.getQueryData(["private"])).toBeUndefined();
    expect(location.href).toContain("/return-here?");
  });
  it("requires explicit host approval and a second user gesture before a registration chooser", async () => {
    mode = "keyless_unenrolled"; mount();
    fireEvent.click(await screen.findByRole("button", { name: "Register passkey" }));
    await screen.findByRole("button", { name: "Check authorization" });
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(`butlers auth authorize-registration --request ${requestId}`);
    expect(chooser).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Check authorization" }));
    await screen.findByText("Waiting for host authorization. Run the command, then check again.");
    expect(chooser).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(c => c[0].endsWith("/ceremony/cancel"))).toBe(true));
    expect(screen.queryByText("Protected dashboard")).toBeNull();
  });
  it("cancels a native sign-in without submitting finish, then retries with a new context", async () => {
    chooser.mockRejectedValue(new DOMException("private-native-error-sentinel", "NotAllowedError")); mount();
    fireEvent.click(await screen.findByRole("button", { name: "Sign in with passkey" }));
    await screen.findByText("Sign-in cancelled. Try again when you are ready.");
    await waitFor(() => expect(fetchMock.mock.calls.some(c => c[0].endsWith("/ceremony/cancel"))).toBe(true));
    expect(screen.queryByText("private-native-error-sentinel")).toBeNull();
    expect(fetchMock.mock.calls.some(c => c[0].endsWith("/login/finish"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Sign in with passkey" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(c => c[0].endsWith("/context"))).toHaveLength(2));
  });
  it("clears a rejected configured key and exposes recovery pending without offering the old passkey", async () => {
    mode = "configured_key"; mount();
    const input = await screen.findByLabelText("Dashboard API key");
    fireEvent.change(input, { target: { value: "synthetic-key-sentinel" } });
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 401 }));
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect((input as HTMLInputElement).value).toBe(""));
    expect(localStorage.getItem("synthetic-key-sentinel")).toBeNull();
    cleanup(); mode = "recovery_pending"; mount();
    await screen.findByRole("button", { name: "Restart host recovery" });
    expect(screen.queryByRole("button", { name: "Sign in with passkey" })).toBeNull();
  });
});
