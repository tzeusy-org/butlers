// ---------------------------------------------------------------------------
// Probe-evidence copy [bu-vpdkk]
//
// Since bu-nz4sn the probe routes publish a member of the backend's closed
// PROBE_FAILURE_VOCABULARY (src/butlers/api/routers/secrets_v2.py) in
// TestResult.message instead of the provider's own words or the credential's
// persisted failure tail. Owner decision Option C (2026-08-13) keeps that free
// text off the wire permanently, so this is the only detail the passport will
// ever have about WHY a probe failed.
//
// That value is a machine token ("rate_limited"), not prose, and ProbeResult
// renders it as serif-italic prose. This module is the one place that turns a
// token into owner-readable copy, shared by every surface that shows probe
// evidence so the two can never drift apart on wording.
//
// Presentation only: nothing here widens the wire. Do not "enrich" this by
// reaching for a provider string, a scope identifier, or an audit note --
// none of them are on the wire, and none may be put back.
// ---------------------------------------------------------------------------

/**
 * Copy for a failing probe with no published category.
 *
 * Used when `TestResult.message` is absent -- which is the permanent state of
 * every per-capability probe row (`CapabilityStatus.test`): the content-blind
 * inventory (bu-iph56) pins that message to null by design, so a capability
 * glyph can say a probe failed and nothing more.
 */
export const PROBE_FAILED_COPY = "probe failed";

/**
 * The backend's PROBE_FAILURE_VOCABULARY, in owner-readable words.
 *
 * Kept in the vocabulary's own order so a reviewer can diff it against
 * secrets_v2.py by eye. Missing members degrade through `probeEvidenceCopy`
 * rather than rendering blank, so a backend that grows the vocabulary is a
 * copy gap here, never a broken surface.
 */
export const PROBE_EVIDENCE_COPY: Record<string, string> = {
  not_set:        "no value is stored",
  expired:        "the stored value has expired",
  rejected:       "the provider rejected this credential",
  rate_limited:   "the provider rate-limited the probe",
  provider_error: "the provider answered with an error",
  malformed:      "the stored value fails its format check",
  unverified:     "no live signal this time; the last live probe had failed",
  other:          "the probe failed for an unclassified reason",
};

/**
 * Render one probe's `message` as owner-readable copy.
 *
 * Total by construction, because the vocabulary is a backend constant that can
 * grow independently of this file:
 *   - a known vocabulary member maps to its copy;
 *   - any other non-empty token is humanised (underscores to spaces) so a
 *     newly added member reads as words rather than as code, and is never
 *     dropped or shown as "undefined";
 *   - null / undefined / blank falls back to {@link PROBE_FAILED_COPY}.
 *
 * Applied regardless of `ok`. The mapping is a property of the field, not of
 * the outcome: the probe routes set `message=None` on success (secrets_v2.py,
 * `probe_user_credential` / `probe_system_credential`), so a successful probe
 * carrying a message does not happen today -- and if one ever did, an
 * `ok`-conditional branch would print the bare token, which is the exact bug
 * this module exists to prevent.
 */
export function probeEvidenceCopy(message: string | null | undefined): string {
  const token = message?.trim();
  if (!token) return PROBE_FAILED_COPY;
  // Object.hasOwn, not a bare index: PROBE_EVIDENCE_COPY is an object literal,
  // so a token that names an Object.prototype member ("constructor",
  // "toString") would index to a function that TypeScript still types as
  // string, and ?? would not fire. Rendering that throws. No backend token
  // looks like that today, but totality is this function's whole contract.
  if (Object.hasOwn(PROBE_EVIDENCE_COPY, token)) return PROBE_EVIDENCE_COPY[token];
  return token.replace(/_/g, " ");
}
