# Implementation and verification tasks

The checkboxes are intentionally open. This change specifies future work; it
does not claim review, approval, implementation, provider access, or live
verification.

## 1. Exact-artifact gates (`bu-7exe4.13.1`)

- [ ] 1.1 Obtain independent security/spec review of the exact candidate head against `REQ-messenger-voice-egress-001` through `REQ-messenger-voice-egress-011`, including the `core-notify` MODIFIED blocks and RFC 0034.
- [ ] 1.2 Obtain separate owner approval of that exact reviewed artifact; record the approved digest without treating approval as provider, presence, credential, endpoint, audio, deployment, or canary authority.
- [ ] 1.3 Immediately before archive, scan every unarchived delta for `Channel Validation`, `Quiet Hours Delivery Gate`, and `Preferred-Channel Resolution on Omitted Channel`; rebuild each block against the then-current baseline and rerun strict/overwrite/countable/citation gates.

## 2. Provider evidence (`bu-7exe4.13.2`)

- [ ] 2.1 Evaluate local Wyoming/satellite first and authenticated Home/HA second against `REQ-messenger-voice-egress-002` and RFC 0034 D4, using content-blind static or separately authorized runtime evidence.
- [ ] 2.2 Record one provider profile as admissible only if start, confirm, safe-before-start, ambiguous, authentication, latency, data-egress, and credential-ownership evidence all pass; otherwise leave voice unavailable.

## 3. Endpoint and presence control plane (`bu-7exe4.13.3`)

- [ ] 3.1 Implement the versioned opaque Messenger endpoint registry and owner-only mutation boundary required by `REQ-messenger-voice-egress-003` and `REQ-messenger-voice-egress-004`, with real-Postgres CAS/concurrency/rollback coverage.
- [ ] 3.2 Implement authenticated `voice_origin.v1` lineage and the Switchboard-brokered Home `voice_presence_attest.v1` exchange required by `REQ-messenger-voice-egress-001` and `REQ-messenger-voice-egress-005`; test wrong service identity, caller-asserted ids, stale/missing/conflicting evidence, nonce replay, and binding-version mismatch.
- [ ] 3.3 Prove no endpoint, room, presence, provider, or credential value reaches model context, API errors, audit free text, logs, metrics, or traces as required by `REQ-messenger-voice-egress-010`.

## 4. Voice delivery and fallback (`bu-7exe4.13.4`)

- [ ] 4.1 Implement the explicit reply/send-only gate and unconditional DND/quiet-hours behavior required by `REQ-messenger-voice-egress-003` and `REQ-messenger-voice-egress-006`; test that priority and approval never bypass suppression and voice never enters defer/coalesce queues.
- [ ] 4.2 Implement the fenced physical-side-effect state machine and replay rules in `REQ-messenger-voice-egress-007` and `REQ-messenger-voice-egress-008`; inject concurrency, timeout, reset, malformed response, crash-after-handoff, confirmed replay, and safe-before-start replay.
- [ ] 4.3 Implement the one separately keyed Switchboard fallback in `REQ-messenger-voice-egress-009`; prove one target maximum, no recursion/cascade, no fallback for invalid origin, and fixed uncertainty copy after failed/ambiguous handoff.
- [ ] 4.4 Implement content-blind receipt cleanup, non-expiring replay tombstones, bounded telemetry, mixed-version gating, and disable-first rollback required by `REQ-messenger-voice-egress-010` and `REQ-messenger-voice-egress-011`.

## 5. Terminal reconciliation (`bu-7exe4.13.5`)

- [ ] 5.1 Map every requirement/scenario to source and behavior-executing tests; negatively sweep for `entity_info` device targets, caller-asserted lineage/presence, proactive voice, generic defer/coalesce, ambiguous retry, content/audio persistence, cloud TTS, direct Messenger-to-Home access, and fallback cascade.
- [ ] 5.2 Confirm manifesto, RFC, OpenSpec, topology, provider evidence, operator recovery, exact-head independent review, and terminal hosted CI agree. Keep any audible canary, deployment, live endpoint/presence read, provider call, credential use, and device mutation as separately authorized actions.
