/** Browser authority lives only in HttpOnly cookies and this module's memory. */
const API_ROOT = import.meta.env.VITE_API_URL ?? "/api";
let csrf: { token: string; expires: number } | null = null;
let csrfPending: Promise<string> | null = null;
let authority = new AbortController();
let generation = 0;
const listeners = new Set<() => void>();

export function onOwnerSessionLost(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function clearOwnerSession(): void {
  generation += 1;
  csrf = null;
  csrfPending = null;
  authority.abort();
  authority = new AbortController();
  for (const listener of listeners) listener();
}

/** Capturing this signal binds asynchronous restoration to one authority generation. */
export function ownerSessionSignal(): AbortSignal {
  return authority.signal;
}

export function rememberOwnerCsrf(tuple: { csrf_token: string; csrf_expires_at: string }): void {
  const expires = Date.parse(tuple.csrf_expires_at);
  if (typeof tuple.csrf_token !== "string" || !tuple.csrf_token || !Number.isFinite(expires) || expires <= Date.now()) {
    throw new Error("Authentication must be restarted.");
  }
  csrf = { token: tuple.csrf_token, expires };
}

export function ownerAuthUrl(path: string): string {
  return `${API_ROOT}/auth/owner${path}`;
}

function sameOrigin(url: string): void {
  if (new URL(url, location.href).origin !== location.origin) {
    throw new Error("The dashboard API must use the same HTTPS origin.");
  }
}

/** Refresh before sending a mutation. Never replay a mutation after a denial. */
async function ownerCsrf(): Promise<string> {
  if (csrf && csrf.expires > Date.now() + 5000) return csrf.token;
  if (csrfPending) return csrfPending;
  const epoch = generation;
  const url = ownerAuthUrl("/csrf");
  sameOrigin(url);
  const pending = (async () => {
    const response = await fetch(url, {
      credentials: "same-origin", cache: "no-store", redirect: "error",
      mode: "cors", signal: AbortSignal.any([authority.signal, AbortSignal.timeout(15_000)]),
      headers: { Accept: "application/json" },
    });
    if (response.status === 401 && epoch === generation) clearOwnerSession();
    if (!response.ok || epoch !== generation) throw new Error("Sign in again before continuing.");
    const { data } = await response.json().catch(() => { throw new Error("Authentication response could not be read."); });
    if (epoch !== generation) throw new Error("Sign in again before continuing.");
    rememberOwnerCsrf(data);
    return data.csrf_token as string;
  })();
  csrfPending = pending;
  try { return await pending; }
  finally { if (csrfPending === pending) csrfPending = null; }
}

/** All protected HTTP calls, including multipart uploads and streamed responses. */
export async function ownerFetch(url: string, options: RequestInit = {}): Promise<Response> {
  sameOrigin(url);
  const epoch = generation;
  const headers = new Headers(options.headers);
  // The browser never carries reusable header authority.
  headers.delete("X-API-Key");
  if (!["GET", "HEAD", "OPTIONS"].includes((options.method ?? "GET").toUpperCase())) {
    headers.set("X-CSRF-Token", await ownerCsrf());
  }
  if (epoch !== generation) throw new DOMException("Session ended", "AbortError");
  const signals = [authority.signal];
  if (options.signal) signals.push(options.signal);
  const response = await fetch(url, {
    ...options, headers: Object.fromEntries(headers), credentials: "same-origin", mode: "same-origin",
    cache: "no-store", redirect: "error", signal: AbortSignal.any(signals),
  });
  if (response.status === 401 && epoch === generation) clearOwnerSession();
  if (response.status === 403 && epoch === generation
    && csrf?.token === headers.get("X-CSRF-Token")) {
    // Another tab can evict this digest. Rehydrate only on the next explicit action.
    csrf = null;
  }
  return response;
}

export async function logoutOwner(all = false): Promise<void> {
  const response = await ownerFetch(ownerAuthUrl(all ? "/sessions" : "/session"), { method: "DELETE" });
  if (!response.ok) throw new Error("Sign out could not be confirmed. Try again.");
  clearOwnerSession();
}
