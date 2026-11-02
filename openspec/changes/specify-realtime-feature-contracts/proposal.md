## Why

Fast sensor transport does not guarantee a timely owner experience, and frequent observations must not become frequent model invocations. The run 23 Home and Travel proposals need testable freshness, response and inference-cost contracts before their feature specifications are adopted.

## What Changes

- Establish the adopted shared consumer contract: deterministic routine observation handling, complete finite response profiles, loss-aware coalescing, aggregate inference budgets and honest latency receipts.
- Apply the contract to appliance handoffs, travel disruption reviews and bounded comfort sessions through the released specification packets, with concrete verification cases in `design.md`.
- Keep operational limit values in versioned runtime configuration. Require each feature proposal to supply its actual initial profile and supported source population before activation; transport defaults are not product guarantees.

Status: **owner-adopted specification; not implemented**. On 2026-10-28 the owner accepted the recommendation to adopt the shared specification and release the three feature-specification tasks: "agreed, please proceed with your recommendation". This adopts REQ-event-response-contract-001 through 005 exactly as reviewed in PR #5073 (source commit `c7979f9aa9c487d34015bdb8aca853549dfcde57`, merged as `980f0f13cbde023021084f1bb4438c64b6c7a0d9`) and authorizes closing specification-release gate `bu-69cc1o`.

The shared requirements are binding on the ensuing feature specifications. Their concrete profiles and domain behavior still require completed specification and exact-text adoption before implementation. This decision adopts no numerical settings and enables no devices, reasoning or notifications. Keep this OpenSpec change active until implementation and verification are complete; adoption is not an archive or delivery receipt.

## Capabilities

### New Capabilities

- `event-response-contract`: Observable freshness, bounded response and aggregate inference-cost guarantees for explicitly enrolled event-driven features, including their deterministic pre-admission paths.

### Modified Capabilities

None. This cross-feature contract supplements, rather than restates, `goal-orchestration` in `generalist-lead-focused-delegation`. Domain deltas remain owned by the existing Home/Travel specification packets. There is no second evaluator, perception store, delivery service or goal runtime.

## Impact

Future consumers include the Home appliance and comfort services and Travel linked-plan impact review. Enforcement must compose existing connector ingestion, domain reducers, RFC 0038 admission/budgets, condition evaluation and delivery. High-frequency alternate paths, particularly HA wellness promotion, must be included in cost attribution rather than assumed free.

This change adds specification documents only. It does not change poll intervals, current Home/Health behavior, authorization, raw-data retention, RFC 0038 first-switch scope, UI components or provider subscriptions. No measured live latency or token savings are claimed.
