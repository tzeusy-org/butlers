# JARVIS pursuit, run 23: realtime outcomes (2026-10-28)

**Reader:** the owner deciding which additional capabilities deserve specification.
**Status:** exploratory, source-grounded proposals; none of the three capabilities below is
claimed delivered. **Epic:** `bu-okh0fw`. **Release gate:** `bu-69cc1o`, assigned to the owner.
Closing the gate starts specification work, not runtime implementation, feature adoption or
deployment. Each resulting contract still needs its exact adoption and implementation allocation.

## Direction

Make existing signals finish useful jobs. The best additional capabilities found in this run
are appliance follow-through, coordinated travel disruption review, and bounded room comfort.
They consume the already planned perception, condition, goal and delivery services. Another
event bus, model lane or general automation engine would duplicate work already owned elsewhere.

The north star remains [earned calm and absorbed mental labor](pursuit-ledger.md#north-star).
For this lens, a useful realtime system notices a meaningful change, identifies the obligation
it affects, and carries that obligation to an observed outcome. Receiving an event, accepting a
command and completing the owner's task are three different things.

Realtime does not mean an unmeasured promise of instant response. Each future feature must
name its supported source, observed and received timestamps, freshness limit, projection time
and owner-delivery time. Ordinary sensor changes stay model-free. Only a meaningful domain
transition should create reasoning or owner attention. Latency targets belong to the existing
owner-latency programme and the actual source's capabilities.

## Scope and evidence

- Source baseline: `04f87c9676d59b313b549fca30a349dc98692ee2`, fetched `origin/main`.
  The earlier root-checkout exploration was not used as the final implementation baseline.
- The owner approved a focused native-agent run in one session in place of the unavailable
  Workflow/ScheduleWakeup tooling. One batch of three agents ran concurrently, then synthesis
  read their atomic disk harvest. Available inherited models replaced the skill's unavailable
  Claude model names. This is a focused run, not the full hourly dashboard audit.
- Standing lenses covered: connectors and proactivity; cross-domain interaction and knowledge
  graph; inference flow. Rotations: home/environment, local mobility, commerce/aftercare and
  travel/logistics. The third lane also examined three coarse owner-surface groups using the
  th-design bar and Dispatch contracts.
- Dedup inputs: all 22 prior runs in the pursuit ledger, the parked ideas ledger, active specs,
  817 live beads, 1,090 recently closed beads since October 14, and 42 open PRs. Full bead text,
  current source and a PR-body follow-up were checked; title similarity alone was not the test.
- Evidence is static and source-confirmed. No live device, flight, private household data,
  provider credentials or deployed latency was inspected. New tables and interfaces named in
  the Beads packets are proposals, not existing capabilities.
- The current skill's Phase 4 output policy supersedes its older tracked-JSON convention:
  this run keeps the Markdown dossier and complete structured Beads fields durable. Local
  harvest/state JSON is resume material only. Separate artifact publication is unavailable
  in this session (`artifact-design` was not found); no local file is represented as a
  published artifact. The repository's rendered dossier is the owner report.

## Ranked moves

Relative value is a planning judgment on a five-point scale; M/L are comparative build costs,
not delivery estimates. All three moves are domain feature extensions. No new trust defect was
established by this focused review, and no UX repair was manufactured to fill a target count.

| Rank | Proposed capability | Owner payoff | Value / build cost | Specification bead |
|---|---|---|---|---|
| 1 | Appliance completion through the human handoff | The washer finishing opens a reminder to transfer the load; only explicit owner evidence closes that chore. | 4 / M | `bu-okh0fw.1` |
| 2 | One trip-disruption review across linked plans | A changed arrival identifies affected pickup, reservation and calendar plans, with exact recovery choices and honest unknowns. | 5 / L | `bu-okh0fw.2` |
| 3 | Bounded comfort sessions with observed outcomes | “Keep the bedroom in this temperature band until 07:00” is measured against the room, with expiry and control relinquishment. | 4 / L | `bu-okh0fw.3` |

1. **Appliance handoff.** Home already receives explicit HA state transitions
   (`src/butlers/connectors/home_assistant_envelope.py:10`), while the inspected activity adapter
   projects motion/entry evidence rather than appliance lifecycle
   (`src/butlers/chronicler/adapters/home_assistant_sensor_activity.py:7`). A supported,
   owner-enrolled appliance would produce one cycle record and one owner handoff obligation.
   Start with read-only enrollment and an explicit started-to-completed state mapping, then add
   Done/Later/Stop watching through the canonical delivery path. Missing start evidence, a stale
   source or unsupported device is unknown, never an inferred successful cycle. No power-pattern
   guessing, appliance control, housemate messaging or inference that an open door means the
   chore is complete. This extends Home's device-monitoring care; the proposed spec must settle
   the narrow human-handoff scope and retention explicitly before implementation.

2. **Trip disruption impact review.** Travel preserves itinerary changes and version conflicts
   (`roster/travel/tools/bookings.py:1260`), but its current connection-risk door deliberately
   acknowledges a risk without taking external action
   (`roster/travel/tools/connections.py:300`, `:515`). Add a Travel-owned review over explicitly
   associated plans, not a second itinerary or generic dependency graph. First ship a read-only
   affected/unaffected/unknown packet. Later, adopted decision sets can offer exact message
   drafts or calendar changes, with separate approvals and owning-service receipts. Travel
   remains authoritative for accepted itinerary observations; Calendar retains event versions.
   A newer delay or owner calendar edit invalidates stale choices before dispatch. No automatic
   booking change, money movement, inferred trip membership or silently substituted recipient.
   This is a concrete successor to the parked life-event ripple idea, distinct from the existing
   adjacent-flight connection calculation and generic goal-replanning machinery.

3. **Comfort session.** Today's correct actuation receipt checks the requested thermostat
   setpoint (`roster/home/modules/actuation.py:313`), not whether the room reached or sustained
   it. Add a finite, owner-authorized session with an explicit sensor/actuator mapping, target
   band and observation interval. Begin with observation-only outcomes, then separately adopt
   bounded corrective action through the existing approval and effect path. Stale evidence
   blocks correction; one control lease fences competing sessions; an unattributed setpoint
   change relinquishes control. Stop prevents new effects and never claims to undo transferred
   heat. Exclude safety/security devices and learned permission. Existing weather preconditioning
   at `src/butlers/jobs/atmosphere_consumers.py:113` remains its own advisory, not a new feature
   claimed here.

Full packets in each bead include outcome/non-goals, governing intent, exact source seams,
proposed schema ownership, failure/concurrency/replay/rollback matrices, documentation changes,
nearest existing tests, and proposed behavior-executing verification. Future implementation test
counts are estimates; this audit adds no product tests and executes no device actions.

## Existing work to finish or explicitly reopen

These remain valuable parts of a realtime, integrated setup. They are not new beads from this run.

| Capability | Existing owner | Why it matters to these proposals |
|---|---|---|
| Typed perception and freshness | `bu-dhloi1.9`, `bu-dhloi1.21` | A stale sensor or corrected source must not look like current truth. |
| Durable conditions and obligations | `bu-dhloi1.11` | Wait for real changes without keeping a model running or inventing another rule engine. |
| Owner latency, streaming and Stop | `bu-dhloi1.12`, `bu-4c7nvp.1.6` | Make interaction responsive; no second fast-answer lane is justified. |
| One delivery identity and surface selection | `bu-dhloi1.13`, `bu-4c7nvp.1.8` | One appliance task or trip review should not become independent cards that disagree. |
| Exact decision sets and completion receipts | `bu-4c7nvp.1.3` | A multi-step recovery needs per-effect consent and truthful partial completion. |
| Places, departure and premeeting cards | `bu-jv1moa.7`, `.8`, `.9`, `.17` | Existing work already covers much of the “help me at the right moment” experience. |
| Purchase aftercare and shipment lifecycle | [Parked domain and connector ideas](../../about/legends-and-lore/ideas-ledger.md#domain-follow-ons-finance-travel-health-education-home-relationship) | Strong future integrated outcome, but source selection, ownership and substrate conditions already exist. Explicitly reopen that lineage rather than rename it. |

The v1 connector list is fixed in `about/heart-and-soul/v1.md`. No new external account or feed
was justified for the first slices above. Appliance support is conditional on a real device
exposing explicit states. The review makes no vendor API, price or rate-limit claim.

## Supporting tier board

Run 22 was a backlog audit with no product tiering; run 21 used architectural questions rather
than these UI cohorts. The last broad tier history is retained in the cumulative ledger.
These narrower snapshots are **not comparable**, so no improvement or regression is claimed.
They are source-confirmed observations, not live usability or accessibility certification.

| Inspected surface cohort | Verdict | Evidence and limit |
|---|---|---|
| Conversation and attention entry | Functional | Shared turn hook and explicit notification errors: `frontend/src/hooks/use-conversation-turn.ts:542`, `frontend/src/pages/NotificationsPage.tsx:458`. Cross-posture continuity remains existing work. |
| Calendar and knowledge evidence entry | Functional | Distinct calendar error/freshness inputs and memory degradation: `frontend/src/pages/CalendarWorkspacePage.tsx:163`, `frontend/src/components/memory/MemoryOverture.tsx:260`. Not a verdict on the entire graph. |
| Home, Travel and Finance inspection tabs | Functional | Actual route is `/butlers/:name`, not three invented top-level pages. Travel list/upcoming polls at 60 seconds (`frontend/src/hooks/use-travel.ts:25`); this is not an end-to-end realtime guarantee. |

## Themes and integration rules

**Observe the result that matters.** A setpoint accepted, a cycle finished and a risk acknowledged
are legitimate receipts, but none completes the broader owner outcome. Keep those meanings
separate in storage and copy. The Home actuation and Travel acknowledgement examples above are
opportunities to extend correct behavior, not findings that those handlers are broken.

**Carry durable responsibility across quiet periods.** Home owns cycles and comfort sessions;
Travel owns impact reviews. Domain-specific rows reference canonical obligations, goals and
delivery identities. Do not mirror another domain's private records or introduce a parallel
queue, evaluator, scheduler, callback family or speculative graph.

**Freshness and revisions constrain action.** Duplicate/reordered events, source outages,
conflicting plans, external control and Stop are first-class acceptance scenarios. Approval binds
the exact current premise and target version; changed evidence requires a new proposal.

**Use one whole-goal lead.** Under `about/heart-and-soul/architecture.md` and RFC 0038, the lead
joins broker-authorized domain evidence. Domains retain responsibilities and effect enforcement.
Third-party observations convey information, not owner instructions or wider authority. None of
these proposals extends the frozen classify-and-route path.

## Release and verification

The owner-held epic contains three spec-first children and one terminal reconciliation,
`bu-okh0fw.4`. All four depend on `bu-69cc1o`. Comfort specification also follows appliance
specification to resolve their shared Home observation/obligation interfaces once. Travel owns a
separate domain contract. Reconciliation compares proposed requirements against the whole
current codebase and identifies exact adoption and implementation prerequisites; it must not
count a merged proposal as delivered functionality.

Runtime programme IDs in the packets are substrate owners, not blanket ancestor dependency
edges. Specification can proceed once released. Before allocating implementation, resolve those
programmes to exact adopted interfaces and descendant blockers; verify the goal-runtime cutover
and relevant M7 scope. Every new behavior remains subject to its own spec adoption.

Verification for this audit: read source at the pinned baseline; check named existing test seams;
deduplicate against full backlog text, parked ideas, active specs and open PR bodies; validate the
structured packets and gate edges; run Beads lint and documentation hygiene before publication.
This is planning evidence. No pytest, live browser, migrated database or device validation is
claimed for the proposed features.

Historical retention was checked at the PR base. Every older dossier body has repository citers
or live Beads citers; the active held programmes remain retained. No old body or historical
evidence pointer needs deletion or rewriting in this run.

## Dropped and retained as prior art

- **Purchase aftercare, shipment feeds, returns/refunds/warranties:** already parked; preserve the
  existing unpark conditions and ownership review.
- **Transit/civic feeds, tariff energy, usage-metered maintenance:** prior proposals; no new
  source was requested or justified by this investigation.
- **Generic weather-smart-home behavior:** already has a preconditioning advisory and drift
  requirements. Only the bounded, observed-result session is new here.
- **New inference lane, warm sessions, event bus, streaming, “Now” card or delivery selector:**
  existing programme owners and, for fast answers, an explicit RFC 0038 D18 contract.
- **Generic answer drift, plan invalidation or life-event ripple:** prior art. The Travel proposal
  owns only a concrete trip-specific consumer and cites that ancestry.
- **Equipment compatibility registry:** insufficient durable ownership or realtime payoff beyond
  an ordinary governed research question; no new asset/compatibility graph proposed.
- **Location-ready errands and trip packs:** overlap existing places, checklists, vendor doors and
  parked Travel work.
- **Power-based guesses that a chore finished:** unsupported by a declared sensor contract;
  explicit machine states and owner handoff evidence are required.
