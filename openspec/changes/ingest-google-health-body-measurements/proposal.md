## Why

The Google Health connector polls sleep, activity and daily vitals but no body measurements, so the
owner logs weight by hand even when a connected scale reports it (bu-q7vx1q.35, run-15 move #7
slice 2). The scope `health_metrics_and_measurements.readonly` is already requested and lists weight
and body fat, so no new consent is needed.

## What Changes

- Two connector bundles, `weight` and `body_fat`, one envelope per sample keyed on its physical time.
- `wellness_ingest` maps them to `measurement_weight` and `measurement_body_fat`.
- `owner_dominance` makes `measurement_latest`, `measurement_history` and `GET /measurements/latest`
  prefer an owner-logged value on the same owner-local day; device rows are kept.

## Capabilities

### Modified Capabilities

- `connector-google-health`: Per-Resource Polling Loops gains the body measurement resources.
- `butler-health`: owner-logged dominance (ADDED requirement).

## Provider contract reconciliation

This proposal retains the original implementation and archive outcome. It corrects the active and
canonical body-measurement plain-list mandate to conform to the existing Reconciled Stream
Consumption requirement: every supported bundle uses its reconcile operation. Weight and body fat
use their existing client's authenticated GET `dataPoints:reconcile` operations in the already
requested scope. It adds no client, model path, grant, scheduler or provider write.

The retained, content-free dev receipt distinguishes the resources:

| Resource | Plain list | Reconcile | What the receipt proves |
| --- | --- | --- | --- |
| `weight` | HTTP 200, nonempty typed point | HTTP 200, nonempty typed point | Reconcile support; `dataPoints`, numeric `weight.weightGrams`, string `sampleTime.physicalTime` and string `nextPageToken` were observed. |
| `body_fat` | HTTP 200, empty object | HTTP 200, empty object | Successful empty responses only; no positive body-fat percentage or sample-time point shape was observed. |

Both successful operation responses support the reconcile selection; empty body-fat output does
not establish the positive mapper schema, prove unsupported reconciliation, or mean a zero or
removed measurement. Weight evidence does not stand in for body-fat evidence. Task 2.3 stays
incomplete until actual permitted content-free positive shape evidence covers both resources.
The retained body-fat unit fixture remains a fixture, not a provider observation. No task state,
archive, activation or operational result is claimed by this correction.

### Existing source and verification handoff

`bu-u5euwb.10` remains the single implementation/verification/archive owner. Its source handoff is the
existing `ResourceBundle` descriptors, `_poll_resource`, `_build_params` and measurement normalizer
in `src/butlers/connectors/google_health.py`, using
`src/butlers/connectors/google_health_client.py::GoogleHealthAPIClient.get_json`. The client already
accepts query parameters; this proposal does not introduce another provider client.

The observed `nextPageToken` makes complete page enumeration an explicit handoff. Its actual
continuation semantics must be verified for the selected operation; the one-page receipt does not
prove them. Provider page tokens are not the durable per-account/resource sample cursor. The
implementation must retain the last committed cursor on incomplete pages or unsuccessful required
submissions, preserve the existing handled-policy-drop rule and replay already accepted samples
with their original keys. Current one-request polling and a submission helper returning after an
error are not evidence of successful complete consumption. The submission seam has separate active
ownership; this proposal does not adopt another branch's helper changes.

The physical sample time remains the external identity and Health `valid_at`; weight remains
converted from grams to kilograms and body fat remains a percentage when its actual point shape
is verified. Stable same-key replay retains existing deduplication. A reconciled read is not a
verified update/deletion feed, and absence or an empty object authorizes no local replacement,
retraction or deletion. This correction creates no changed-payload or changed-identity policy.
Health's owner-local-day dominance, source stamps, privacy, account isolation and retained device
rows remain unchanged.

Verification belongs to the existing owners:
`tests/connectors/test_google_health_body_measurements.py`,
`tests/connectors/test_google_health_connector.py`,
`roster/health/tests/test_wellness_ingest_body_measurements.py` and
`roster/health/tests/test_owner_dominance.py`. Their handoff covers actual operation/envelope
selection, empty responses without writes, complete pages and failure before checkpoint commit,
sample-time normalization, same-key replay and distinct same-day samples, privacy and manual-value
dominance. New replacement/retraction semantics must not be invented to make those tests pass.

The retained source CI run 40046070833 attempt 1 passed all ten owner-dominance cases, including the
real-Postgres survivor, non-skipped. Its eight relevant feature/test blobs match the source used for
this correction. That is historical consumer evidence; it does not prove this endpoint change,
current dependency/migration runtime, pagination or live body-fat shape. Resulting implementation
checks and hosted evidence remain with the original whole outcome.

### Evidence, adoption and rollback boundaries

The status/type-only provider receipt and original implementation manifest/report are retained
outside the repository as `body-measurements10-implementation/provider-shape-result.json`,
`manifest.json` and `report.md`; the separate runtime receipt is
`body-measurements10-pg-fresh-runtime.json`. Exact hashes and source bindings accompany the
independent review, without health samples, account identifiers, tokens or raw responses in the
source proposal.

This is a conformance proposal under the existing supported-reconcile requirement. Independent
review must check the exact normative text, all original clauses/scenarios and retained evidence
before the coordinator records adoption. It neither invokes a blanket security-policy approval nor
supplies an owner decision for new measurement replacement/retraction behavior. Strict validation,
requirement trace and applicable guards are necessary source checks, not provider or runtime proof.
Rollback is the reviewed three-file spec/proposal diff; it does not change provider state, stored
samples or durable cursors. The original tasks, Health delta, source implementation and final
archive/activation boundaries remain intact.

Tests: +0 ~0 -0 for this contract/evidence proposal. Implementation reports its actual executing
survivors and test delta separately.
