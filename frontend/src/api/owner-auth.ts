import { ownerAuthUrl, ownerSessionSignal, rememberOwnerCsrf } from "./owner-session";

export type OwnerState = "keyless_unenrolled" | "keyless_enrolled" | "configured_key" | "recovery_pending" | "unavailable";
export interface OwnerStatus { state: OwnerState; authenticated: boolean; session_expires_at: string | null }
export interface Intent { request_id: string; expires_at: string; operation: "enroll" | "recover"; canonical_origin: string }
export interface Ceremony { ceremony_id: string; publicKey: Record<string, unknown> }
export interface SessionTuple { csrf_token: string; csrf_expires_at: string; session_expires_at: string }

/** Fixed messages only: auth response bodies and native verifier errors never reach UI/loggers. */
export class OwnerAuthError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(status === 429 ? "Too many attempts. Wait one minute, then try again."
      : status === 409 ? "This request expired or was already used. Start again."
      : status === 403 ? "This browser could not be verified. Start again from the canonical HTTPS address."
      : status === 401 ? "Access could not be verified. Start again."
      : "Authentication is unavailable. Check the connection and try again.");
    this.status = status;
  }
}

export async function authRequest<T>(path: string, body?: unknown, csrf?: string, signal?: AbortSignal): Promise<T> {
  const url = ownerAuthUrl(path);
  if (new URL(url, location.href).origin !== location.origin) throw new OwnerAuthError(503);
  const headers = new Headers({ Accept: "application/json" });
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(url, {
    method: body === undefined ? "GET" : "POST", headers,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    credentials: "same-origin", cache: "no-store", redirect: "error", mode: "cors",
    signal: AbortSignal.any([AbortSignal.timeout(15_000), ...(signal ? [signal] : [])]),
  });
  if (!response.ok) throw new OwnerAuthError(response.status);
  return (await response.json().catch(() => { throw new Error("Authentication response could not be read."); })).data as T;
}

export async function ownerStatus(signal?: AbortSignal): Promise<OwnerStatus> {
  const value = await authRequest<OwnerStatus>("/status", undefined, undefined, signal);
  if (!["keyless_unenrolled", "keyless_enrolled", "configured_key", "recovery_pending", "unavailable"].includes(value.state)
    || typeof value.authenticated !== "boolean"
    || (value.authenticated && (!value.session_expires_at || !Number.isFinite(Date.parse(value.session_expires_at)) || Date.parse(value.session_expires_at) <= Date.now()))) {
    throw new OwnerAuthError(503);
  }
  return value;
}

export async function restoreOwnerSession(signal?: AbortSignal): Promise<OwnerStatus> {
  const current = AbortSignal.any([ownerSessionSignal(), ...(signal ? [signal] : [])]);
  const status = await ownerStatus(current);
  current.throwIfAborted();
  if (status.authenticated) {
    const tuple = await authRequest<SessionTuple>("/csrf", undefined, undefined, current);
    current.throwIfAborted();
    rememberOwnerCsrf(tuple);
  }
  return status;
}

function decode(value: unknown, max = 65536): ArrayBuffer {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value) || value.length > max * 4 / 3 + 4) throw new OwnerAuthError(400);
  const raw = atob(value.replace(/-/g, "+").replace(/_/g, "/"));
  const result = Uint8Array.from(raw, c => c.charCodeAt(0)).buffer;
  if (encode(result) !== value || result.byteLength > max) throw new OwnerAuthError(400);
  return result;
}
function encode(value: ArrayBuffer): string {
  if (!(value instanceof ArrayBuffer) || value.byteLength > 65_536) throw new OwnerAuthError(413);
  return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function registrationOptions(ceremony: Ceremony): PublicKeyCredentialCreationOptions {
  const key = ceremony.publicKey;
  const user = key.user as { id: string; name: string; displayName: string };
  return { ...key, challenge: decode(key.challenge, 32), user: { ...user, id: decode(user.id, 32) } } as PublicKeyCredentialCreationOptions;
}
export function loginOptions(ceremony: Ceremony): PublicKeyCredentialRequestOptions {
  return { ...ceremony.publicKey, challenge: decode(ceremony.publicKey.challenge, 32) } as PublicKeyCredentialRequestOptions;
}

/** Positive wire projection; never spread native toJSON() or extension output. */
export function credentialWire(value: Credential | null, operation: "register" | "login"): Record<string, unknown> {
  if (!value || value.type !== "public-key") throw new OwnerAuthError(400);
  const credential = value as PublicKeyCredential;
  const id = encode(credential.rawId);
  // Bitwarden includes credProps: undefined when no extension was requested.
  const hasExtensionData = Object.values(credential.getClientExtensionResults()).some(value => value !== undefined);
  if (id !== credential.id || !credential.rawId.byteLength || credential.rawId.byteLength > 1023 || hasExtensionData) throw new OwnerAuthError(400);
  if (credential.authenticatorAttachment && !["platform", "cross-platform"].includes(credential.authenticatorAttachment)) throw new OwnerAuthError(400);
  const response = credential.response;
  const clientDataJSON = encode(response.clientDataJSON);
  if (response.clientDataJSON.byteLength > 4096) throw new OwnerAuthError(400);
  let wireResponse: Record<string, unknown>;
  if (operation === "register") {
    const attestation = response as AuthenticatorAttestationResponse;
    const transports = attestation.getTransports?.() ?? [];
    if (transports.some(t => !["usb", "nfc", "ble", "internal", "hybrid", "smart-card"].includes(t))) throw new OwnerAuthError(400);
    wireResponse = { clientDataJSON, attestationObject: encode(attestation.attestationObject), transports };
  } else {
    const assertion = response as AuthenticatorAssertionResponse;
    if (!assertion.userHandle || assertion.userHandle.byteLength !== 32) throw new OwnerAuthError(400);
    wireResponse = { clientDataJSON, authenticatorData: encode(assertion.authenticatorData), signature: encode(assertion.signature), userHandle: encode(assertion.userHandle) };
  }
  const wire = { id, rawId: id, type: "public-key", response: wireResponse, clientExtensionResults: {},
    ...(credential.authenticatorAttachment ? { authenticatorAttachment: credential.authenticatorAttachment } : {}) };
  if (JSON.stringify(wire).length > (operation === "register" ? 64 * 1024 : 16 * 1024) - 128) throw new OwnerAuthError(413);
  return wire;
}
