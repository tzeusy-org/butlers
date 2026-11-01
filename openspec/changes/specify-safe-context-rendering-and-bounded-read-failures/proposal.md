## Why

The accepted preamble example and current Context Preamble requirement require arbitrary values, while General's calendar producer emits event titles into those values and metadata. A third-party title can therefore enter an instruction-bearing session preamble. Current read helpers also collapse an unavailable store into no suppression; the sleep producer clears on read failure. This contradicts the approved security intent of bu-dhloi1.17 and its implementation successor bu-dhloi1.2.

## What Changes

- Replace raw-value rendering with an enumerated, producer-bound typed projection; initially only calendar meeting/busy and focused/focused values qualify.
- Keep calendar title classification internal; emit no title/free-text value, metadata or result.
- Require the actual bound owner request for model-mediated set/clear. Preserve deterministic producers and the existing canonical DND gateway.
- Separate successful invalid/absent policy from transient unavailable reads, preserve confirmed finite expiry without renewal, and require fresh evaluation on recovery.
- Preserve deterministic authorized safety bypass and all existing TTL/max-hold numbers and timezone rules.

This is an exact-text DRAFT amendment, not implementation or adoption. RFC0009's changed clauses remain proposed until the owner adopts this concrete revision after independent conformance review. The accepted remainder of RFC0009 and RFC0038 stay binding. The main context-bus spec is not prematurely synced; the active MODIFIED blocks replace the named requirements only when adopted/applied.

## Capabilities

### Modified Capabilities
- `context-bus`: preamble rendering, deterministic calendar output, owner-mediated writes, sleep-policy failure distinction and bounded suppression-read failure.

## Impact

Contract-only changes to RFC0009, this active change and context/attention documentation. The current code-bound defaults are cited in design.md. Future implementation is bu-dhloi1.2 in its own PR, including same-PR test amendments and genuine authority/DB evidence where needed.

No new signal types, perception store, timezone, safety category, duration setting, notification route, schema, deployment, switch execution or production activation. Tests: +0 ~0 -0. Existing tests are future implementation seams, not passes asserted by this authoring task.
