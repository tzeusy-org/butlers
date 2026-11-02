/** A fresh owner assertion authorizes only the exact server-derived rule intent. */
import { credentialWire, loginOptions, OwnerAuthError, type Ceremony } from "./owner-auth";
import { ownerAuthUrl, ownerFetch, ownerSessionSignal } from "./owner-session";

type Kind = "create" | "from_action" | "approve_with_rule" | "promotion";

async function post<T>(url: string, body: unknown, signal: AbortSignal): Promise<T> {
  const response = await ownerFetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]),
  });
  if (!response.ok) throw new OwnerAuthError(response.status);
  const envelope: unknown = await response.json();
  if (!envelope || typeof envelope !== "object" || !("data" in envelope)) throw new OwnerAuthError(503);
  return envelope.data as T;
}

/** Never cache the nonce, retry a mutation, or infer a factor from a session cookie. */
export async function standingRuleHeaders(kind: Kind, parameters: object): Promise<Record<string, string>> {
  const signal = ownerSessionSignal();
  const base = import.meta.env.VITE_API_URL ?? "/api";
  const options = await post<Ceremony | { required: false }>(`${base}/approvals/step-up/options`, { kind, parameters }, signal);
  signal.throwIfAborted();
  // Only a server-classified demotion can keep its existing one-tap door.
  if ("required" in options && options.required === false && kind === "promotion") return {};
  if (!("ceremony_id" in options) || typeof options.ceremony_id !== "string") throw new OwnerAuthError(503);
  const credential = await navigator.credentials.get({ publicKey: loginOptions(options), signal });
  signal.throwIfAborted();
  const proof = await post<{ nonce: string; expires_at: string }>(ownerAuthUrl("/step-up/finish"), {
    ceremony_id: options.ceremony_id, credential: credentialWire(credential, "login"),
  }, signal);
  signal.throwIfAborted();
  if (!/^[A-Za-z0-9_-]{43}$/.test(proof.nonce)
    || !Number.isFinite(Date.parse(proof.expires_at)) || Date.parse(proof.expires_at) <= Date.now()) throw new OwnerAuthError(409);
  return { "X-Dashboard-Step-Up": proof.nonce };
}
