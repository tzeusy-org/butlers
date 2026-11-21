## Why

The insight broker learns whether a nudge helped from one proxy: any owner message to any butler
within 60 minutes of a delivery marks it engaged (`check_and_update_engagement`). That cannot tell a
nudge that changed something from one that resolves itself, such as an autopaid bill, so the owner
keeps being interrupted about things that would have happened anyway, and nudges that work can be
muted by a noisy proxy. Vision's success markers ask the system to reduce frequency when the owner
disengages and to measure success by the mental labour absorbed (bu-s11n0s.15, JARVIS pursuit
run 16).

Premise-bound insights already name the fact they assert (core_255), and the broker already holds
some candidates back for budget or context, which are natural holdouts. Comparing how often and how
fast the premise resolves with and without the nudge answers the question honestly, without
withholding anything deliberately.

## What changes

Slice 1 (this change, observe-only, no delivery change):

- `core_353` adds `public.insight_outcomes` (one row per terminal premised candidate, keyed by
  candidate id with no foreign key so it outlives the candidate pruner) and
  `public.insight_class_efficacy` (the per-class verdict rollup slice 2 fills). Switchboard only,
  fenced by row level security.
- `butlers.tools.switchboard.insight.outcomes`: enrollment with an exposure taxonomy (treated:
  delivered, coalesced; natural holdouts: held by budget or context; excluded: withdrawn,
  shadowed, held by an endpoint custody hold, unverified, failed) and a sweep that records once
  whether the premise resolved, when, and whether that time is exact or only observed by the
  sweep. Unknown reads settle `unmeasurable`, never "ineffective".
- A daily deterministic `insight_outcome_sweep` Switchboard job at 05:52 UTC, before the 06:15
  delivery window.

Slice 2 (observe-only, no delivery change):

- After each sweep, the job rolls settled rows up into `public.insight_class_efficacy` per class
  and priority band (effective, self_resolving, ignored, unmeasured; at least five per arm,
  compared only within one band, labelled observational).
- `GET /api/attention/efficacy` returns the latest row per class and band with the degraded
  envelope, and the Trust Console's attention-ledger panel lists the rows. No model-reachable tool
  (RFC 0038 regroom).

Slices 3-5 (owner decision 2026-11-21, `insight-outcome-shaping` = A; shaping only under the
owner-only `outcome_shaping=shape`, default `observe`):

- `core_354` adds `insight_settings.outcome_shaping` (`observe` | `shape`) and `shaping_state`
  (per class: the verdict seen, since when, and when the owner was last told). Nothing in the
  repository writes `outcome_shaping`; turning `shape` on is an owner act.
- `shaping.py`: under `shape` a routine cycle makes a fresh `self_resolving` class digest-only
  (never standalone, no slot, it rides a delivery already going out or stays pending), restores the
  baseline weight of an `effective` class over the click proxy, and cites the counts in
  `category_budget_reasons`. Never the urgent band, the break-through category or the urgent-only
  sub-cycle; a verdict older than 14 days lapses; a `useful` reply after the demotion began
  restores the class.
- One line per class change (`self_resolving`, or `ignored` with no feedback), at most once per
  30 days, riding a delivery, with the existing useful (keep) and mute (quiet) doors.
- A delivery whose class has a fresh measured verdict in its own band gets no engagement row (so
  the proxy, the category weight and the disengagement rate never count it); riders take no
  daily send slot.

## Deferred

- None. Setting `outcome_shaping` to `shape` in any environment stays an owner act.

## Conformance

Requirement 039 (slices 3-5) conforms to the owner's recorded boundary of 2026-11-21: a
`self_resolving` verdict demotes a class to digest-only and an `effective` one overrides the click
proxy only under the owner-only `outcome_shaping=shape`, the urgent band is never shaped, each
class change is one digest line per 30 days citing counts with keep/quiet replies, and the proxy
is retired only for covered classes. It adds no route, session, delegation, model or
model-reachable tool, so it stays inside RFC 0038's frozen classify-and-route (fixes only). Choices
the boundary leaves open, kept conservative: a verdict older than 14 days lapses; digest-only means
"rides an existing delivery, never its own, never a slot"; the break-through category is exempt;
`keep` is the existing `useful` verb and `quiet` the existing `never` verb.

The first two requirements conform to the bead's adopted design (bu-s11n0s.15 regroom, 2026-10-16) and
to RFC 0011 as RFC 0038 keeps it ("Remains; candidates carry taint"). It is deterministic, adds no
route, session, delegation or model-reachable tool, and changes no delivery, so it is conforming
text under the recorded owner decision for conforming doctrine and spec text.

Beyond the adopted design, the requirement adds conservative refinements that only ever exclude
an observation or settle it as unknown, never count it in an arm:

- three excluded exposures the design does not name: `held_custody` (delivery refused by an
  endpoint custody hold), `held_unverified` (the premise could not be checked at send) and
  `failed` (the transport gave up);
- the SHALL line that a custody-refused delivery is `held_custody`, never a holdout, because a
  lost-device window says nothing about the owner's attention;
- a probe flip first observed after the window closed is never `resolved`: it is `persisted`
  when a check at or after the window end saw the premise holding, otherwise `unmeasurable`.

The adopted design names the verdicts, the five-per-arm floor and the priority bands but not the
cut points. Slice 2 picks them and keeps them in code constants, not spec text: `effective` needs
a 20-point resolve-rate gap or, at a comparable rate, a treated median at most half the holdout's;
`self_resolving` needs a holdout rate of at least 60% with the treated arm not resolving clearly
more often (or clearly faster); the rollup reads 90 days of settled rows. They only label an
observation and change no delivery.

## Impact

- Code: `alembic/versions/core/core_353_insight_outcomes.py`,
  `roster/switchboard/tools/insight/outcomes.py`, `src/butlers/scheduled_jobs.py`,
  `roster/switchboard/butler.toml`, `src/butlers/db_authority/grants.py`.
- Docs: `docs/data_and_storage/schema-topology.md`, `docs/operations/data-retention.md`.
- Slice 2 code: `roster/switchboard/tools/insight/outcomes.py` (`compute_class_efficacy`),
  `src/butlers/api/routers/attention_ledger.py`, `src/butlers/api/models/attention_ledger.py`,
  `frontend/src/components/approvals/attention-ledger-panel.tsx`; docs
  `docs/butlers/switchboard.md`.
- Slices 3-5 code: `alembic/versions/core/core_354_insight_outcome_shaping.py`,
  `roster/switchboard/tools/insight/shaping.py`, `roster/switchboard/tools/insight/broker.py`
  (`delivery_cycle`, `check_and_update_engagement`); RFC 0011 amendment 9.
- Specs: `proactive-insight-engine` gains three requirements.
