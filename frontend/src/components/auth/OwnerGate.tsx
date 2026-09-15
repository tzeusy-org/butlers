import { useDarkMode } from "@/hooks/useDarkMode";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { authRequest, credentialWire, loginOptions, ownerStatus, OwnerAuthError, registrationOptions, restoreOwnerSession, type Ceremony, type Intent, type OwnerStatus, type SessionTuple } from "@/api/owner-auth";
import { clearOwnerSession, onOwnerSessionLost, rememberOwnerCsrf } from "@/api/owner-session";

/** Mounted above every private hook and route. Auth results never enter QueryClient. */
export function OwnerGate({ children }: { children: ReactNode }) {
  useDarkMode();
  const cache = useQueryClient();
  const [status, setStatus] = useState<OwnerStatus | null>(null);
  const [message, setMessage] = useState("Checking access…");
  const mounted = useRef(true);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await restoreOwnerSession(signal);
      if (signal?.aborted || !mounted.current) return;
      setStatus(next); setMessage("");
    } catch {
      if (signal?.aborted || !mounted.current) return;
      setStatus({ state: "unavailable", authenticated: false, session_expires_at: null });
      setMessage("Authentication is unavailable. Check the connection and try again.");
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    const unsubscribe = onOwnerSessionLost(() => {
      void cache.cancelQueries(); cache.clear();
      setStatus(previous => previous ? { ...previous, authenticated: false, session_expires_at: null } : null);
      setMessage("Your session ended. Sign in again.");
      void refresh(controller.signal);
    });
    queueMicrotask(() => { if (!controller.signal.aborted) void refresh(controller.signal); });
    return () => { mounted.current = false; controller.abort(); unsubscribe(); };
  }, [cache, refresh]);
  useEffect(() => {
    if (!status?.authenticated || !status.session_expires_at) return;
    const deadline = setTimeout(clearOwnerSession, Math.max(0, Date.parse(status.session_expires_at) - Date.now()));
    const controller = new AbortController();
    const verify = () => {
      if (document.visibilityState === "hidden") return;
      void ownerStatus(controller.signal).then(next => {
        if (!controller.signal.aborted && !next.authenticated) clearOwnerSession();
      }).catch(() => { if (!controller.signal.aborted) clearOwnerSession(); });
    };
    const poll = setInterval(verify, 60_000);
    document.addEventListener("visibilitychange", verify); window.addEventListener("focus", verify);
    return () => { clearTimeout(deadline); clearInterval(poll); controller.abort(); document.removeEventListener("visibilitychange", verify); window.removeEventListener("focus", verify); };
  }, [status]);
  if (status?.authenticated) return children;
  return <OwnerAccess status={status} initialMessage={message} onAuthenticated={refresh} />;
}

function OwnerAccess({ status, initialMessage, onAuthenticated }: {
  status: OwnerStatus | null; initialMessage: string; onAuthenticated: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [intent, setIntent] = useState<Intent | null>(null);
  const [ceremony, setCeremony] = useState<Ceremony | null>(null);
  const [nextCheck, setNextCheck] = useState(0);
  const [now, setNow] = useState(Date.now);
  const preauth = useRef<string | undefined>(undefined);
  const pending = useRef<{ request_id: string } | { ceremony_id: string } | null>(null);
  const flight = useRef<AbortController | null>(null);
  const action = useRef<HTMLButtonElement>(null);
  const secure = window.isSecureContext && location.protocol === "https:" && (!location.port || location.port === "443");
  const passkeys = secure && typeof PublicKeyCredential !== "undefined" && !!navigator.credentials;
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => () => { flight.current?.abort(); preauth.current = undefined; }, []);
  useEffect(() => { if (!busy) action.current?.focus(); }, [busy, intent, ceremony]);

  async function abandon() {
    const target = pending.current; const token = preauth.current;
    pending.current = null; preauth.current = undefined;
    if (target && token) {
      try { await authRequest("/ceremony/cancel", target, token); } catch { /* A cancel cannot undo committed authority. */ }
    }
  }
  async function context(signal: AbortSignal) {
    await abandon();
    const tuple = await authRequest<SessionTuple>("/context", {}, undefined, signal);
    preauth.current = tuple.csrf_token;
  }
  async function run(task: (signal: AbortSignal) => Promise<void>, label: string) {
    if (flight.current) return;
    const controller = new AbortController(); flight.current = controller;
    setBusy(true); setMessage(label);
    try { await task(controller.signal); }
    catch (error) {
      if (!controller.signal.aborted) {
        if (error instanceof DOMException && ["NotAllowedError", "AbortError"].includes(error.name)) {
          setMessage(intent ? "Registration cancelled. Start again when you are ready." : "Sign-in cancelled. Try again when you are ready.");
          await abandon(); setIntent(null); setCeremony(null); await onAuthenticated();
        } else {
          setMessage(error instanceof OwnerAuthError ? error.message : "The request could not be completed. Check the connection and try again.");
          if (error instanceof OwnerAuthError && error.status === 429) setNextCheck(Date.now() + 60_000);
          if (error instanceof OwnerAuthError && [401, 403, 409].includes(error.status)) {
            await abandon(); setIntent(null); setCeremony(null); await onAuthenticated();
          }
        }
      }
    } finally {
      if (flight.current === controller) { flight.current = null; setBusy(false); action.current?.focus(); }
    }
  }
  async function finish(path: string, current: Ceremony, value: Credential | null, signal: AbortSignal) {
    const credential = credentialWire(value, path === "/registration/finish" ? "register" : "login");
    try {
      const tuple = await authRequest<SessionTuple>(path, { ceremony_id: current.ceremony_id, credential }, preauth.current, signal);
      rememberOwnerCsrf(tuple); pending.current = null; preauth.current = undefined;
      await onAuthenticated();
    } catch (error) {
      if (error instanceof OwnerAuthError) throw error;
      // A lost response may follow a committed enrollment. Never resubmit finish.
      pending.current = null; preauth.current = undefined; setIntent(null); setCeremony(null);
      setMessage("Completion could not be confirmed. Checking this browser's session; otherwise sign in with your passkey.");
      await onAuthenticated();
    }
  }
  const startLogin = () => run(async signal => {
    await context(signal);
    const current = await authRequest<Ceremony>("/login/options", {}, preauth.current, signal);
    pending.current = { ceremony_id: current.ceremony_id };
    const value = await navigator.credentials.get({ publicKey: loginOptions(current), signal });
    await finish("/login/finish", current, value, signal);
  }, "Waiting for your passkey…");
  const startRegistration = (operation: "enroll" | "recover") => run(async signal => {
    await context(signal);
    const next = await authRequest<Intent>("/registration/intent", { operation }, preauth.current, signal);
    if (!/^[A-Za-z0-9_-]{43}$/.test(next.request_id) || next.canonical_origin !== location.origin || next.operation !== operation || !Number.isFinite(Date.parse(next.expires_at))) throw new OwnerAuthError(503);
    pending.current = { request_id: next.request_id }; setIntent(next); setCeremony(null);
    setMessage("Run the command on the Butlers host, then check authorization.");
  }, "Preparing your registration request…");
  const check = () => run(async signal => {
    if (!intent) return;
    setNextCheck(Date.now() + 2000);
    const result = await authRequest<Ceremony | { state: "pending"; expires_at: string }>("/registration/options", { request_id: intent.request_id }, preauth.current, signal);
    if ("state" in result) { setMessage("Waiting for host authorization. Run the command, then check again."); return; }
    pending.current = { ceremony_id: result.ceremony_id }; setCeremony(result);
    setMessage("Host authorization confirmed. Choose Bitwarden to save your passkey.");
  }, "Checking host authorization…");
  const register = () => run(async signal => {
    if (!ceremony) return;
    const value = await navigator.credentials.create({ publicKey: registrationOptions(ceremony), signal });
    await finish("/registration/finish", ceremony, value, signal);
  }, "Waiting for your passkey…");
  const cancel = () => {
    flight.current?.abort(); flight.current = null; setBusy(false);
    setMessage(intent ? "Registration cancelled. Host recovery, if approved, remains in effect." : "Sign-in cancelled.");
    setIntent(null); setCeremony(null); void abandon().then(() => onAuthenticated());
  };
  const expired = intent && Date.parse(intent.expires_at) <= now;
  const command = intent ? `butlers auth ${intent.operation === "recover" ? "authorize-recovery" : "authorize-registration"} --request ${intent.request_id}${intent.operation === "recover" ? " --confirm-revoke" : ""}` : "";
  const configured = status?.state === "configured_key";
  const enrolled = status?.state === "keyless_enrolled";
  const unavailable = !status || status.state === "unavailable";

  return <div className="min-h-dvh bg-background text-foreground">
    <header className="flex h-14 items-center border-b border-border px-6 font-semibold">Butlers</header>
    <main className="mx-auto max-w-xl space-y-6 px-6 py-12" aria-labelledby="owner-access-title">
      <div className="space-y-2"><p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">Owner access</p>
        <h1 id="owner-access-title" className="text-2xl font-bold tracking-tight">{configured || enrolled ? "Sign in to Butlers" : "Set up your passkey"}</h1>
        <p className="text-sm text-muted-foreground">{configured ? "Use the dashboard key to start a secure browser session." : enrolled ? "Use your saved passkey, including from a new browser with Bitwarden sync." : "Authorize this browser on your Butlers host, then choose Bitwarden to save your passkey."}</p>
      </div>
      {!secure && <p role="alert" className="text-sm">Open the canonical Tailscale Serve HTTPS address on port 443. Browser sign-in is unavailable on this address.</p>}
      {secure && !configured && !passkeys && <p role="alert" className="text-sm">This browser does not support passkeys. Open the canonical HTTPS address in a browser with WebAuthn support.</p>}
      <p role="status" aria-live="polite" className="min-h-6 text-sm">{message || initialMessage}</p>
      {unavailable ? <Button ref={action} disabled={busy} onClick={() => void run(() => onAuthenticated(), "Checking access…")}>Retry access check</Button>
        : configured ? <form className="space-y-4" onSubmit={event => {
          event.preventDefault(); const key = apiKey;
          void run(async signal => {
            try { const tuple = await authRequest<SessionTuple>("/session", { api_key: key }, undefined, signal); rememberOwnerCsrf(tuple); await onAuthenticated(); }
            finally { setApiKey(""); }
          }, "Signing in…");
        }}>
          <label htmlFor="owner-api-key" className="block space-y-2 text-sm">Dashboard API key<Input id="owner-api-key" type="password" value={apiKey} autoComplete="off" spellCheck={false} onChange={e => setApiKey(e.target.value)} disabled={busy || !secure} /></label>
          <Button ref={action} type="submit" disabled={!secure || busy || !apiKey}>Sign in</Button>
          <p className="text-sm text-muted-foreground">Automation continues to use X-API-Key. Passkey mode requires a deliberate host configuration change.</p>
        </form>
        : intent ? <section className="space-y-4" aria-label="Host authorization">
          <p className="text-sm">Canonical origin: <span className="font-mono break-all">{intent.canonical_origin}</span></p>
          {intent.operation === "recover" && <p className="text-sm">Host approval immediately revokes the old passkey and all browser sessions. Interrupted recovery needs another host recovery command.</p>}
          <label className="block space-y-2 text-sm">Run on your Butlers host<textarea readOnly value={command} rows={4} className="w-full rounded-md border border-input bg-background p-3 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" /></label>
          <p className="text-sm text-muted-foreground">Approve only the request shown in this browser. Never run a registration command supplied by someone else.</p>
          {expired ? <><p role="alert" className="text-sm">This request expired. Start again to request host authorization.</p><Button ref={action} onClick={cancel}>Start again</Button></>
            : <Button ref={action} disabled={busy || now < nextCheck || !passkeys || document.visibilityState === "hidden"} onClick={() => void (ceremony ? register() : check())}>{ceremony ? "Register passkey" : "Check authorization"}</Button>}
        </section>
        : <div className="space-y-4">
          {enrolled ? <Button ref={action} disabled={busy || !passkeys || now < nextCheck} onClick={() => void startLogin()}>Sign in with passkey</Button>
            : <Button ref={action} disabled={busy || !passkeys || now < nextCheck} onClick={() => void startRegistration(status.state === "recovery_pending" ? "recover" : "enroll")}>{status.state === "recovery_pending" ? "Restart host recovery" : "Register passkey"}</Button>}
          {enrolled && <div className="space-y-2"><p className="text-sm text-muted-foreground">Lost access to your passkey or vault? Recovery requires your Butlers host and replaces the old credential.</p><Button variant="outline" disabled={busy || !passkeys} onClick={() => void startRegistration("recover")}>Recover access</Button></div>}
          {status.state === "recovery_pending" && <p className="text-sm">Recovery is pending. The previous passkey no longer works; authorize a replacement on the host.</p>}
        </div>}
      {(busy || intent) && <Button variant="ghost" onClick={cancel}>Cancel</Button>}
    </main>
  </div>;
}
