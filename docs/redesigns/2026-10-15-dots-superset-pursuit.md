# Dots superset pursuit: run 20 (2026-10-15)

**Reader:** owner deciding which Dots-superset capabilities to release. **Status:** proposed planning record; not adopted requirements or deployment evidence. Researched at baseline `1fe05f13c` (Asia/Singapore, 2026-10-15 11:12 to about 20:40).

A non-standard-lens run of the JARVIS pursuit, aimed at the owner's goal: Butlers should be a superset of ChatGPT Dots, with access to more APIs and more freeform reach, inside its doctrine. It starts from the Dots capability gap study (epics `bu-svqpzo`, `bu-6qaju2`, `bu-kqme7f`, `bu-z0b0ah`) and asks what Butlers could do that Dots cannot, and what the filed beads still miss. Twenty-one read-only agents researched it; a critic tried to falsify the boldest claims. 86 candidate moves reduce to fifteen ranked moves, six owner decisions and 36 vetted follow-ups, deduplicated against each other, every non-closed bead and prior pursuits.

Naming: the run's working files, workflow name and some bead notes written during the run call it "pursuit run 19". The ledger already had a run 19 (QA remediation, `bu-6a9xxw`), so this is run 20.

Structured evidence: `git show 3df72ae7c552:docs/redesigns/2026-10-15-dots-superset-pursuit-data.json` (the file left the tree under the two-newest-runs retention rule when run 22 landed).

## Decision in view

Epic `bu-jv1moa` is held by owner gate `bu-4ap4ap`. Closing the gate releases sixteen children (fifteen moves plus one terminal reconciliation); the owner may instead close only chosen children. Every child carries a full Dispatch Readiness Packet in its structured fields (`bd lint`: zero warnings on 18 beads). The six owner decisions below are not children and are not released by the gate. Amendments a move carries land only with the owner's approval of their exact text.

## North star

Butlers is a superset of Dots for one owner: everything Dots does for a person (research, finished files, mail, monitoring, long projects) Butlers does with more reach (the house, the phone, money, health, place) and stronger guarantees: the owner sees the exact call before it runs, every live link says where it came from, nothing spends, nothing reaches another person without the owner, and the fleet never outspends the owner's ceiling.

## Where Butlers stands against Dots

The study's 50 Dots use cases, scored by the `coverage-reach` lens. Of the 22 that still fail, 14 are owner exclusions (purchases, Slack and similar) and 6 are code or work-tool doctrine; none is a gap Butlers wants to close.

| Point in time | Complete | Partial | Fail |
|---|---|---|---|
| Study (2026-10-15) | 5 | 18 | 27 |
| After every Dots-gap bead lands | 16 | 12 | 22 |
| Plus this run's file moves and the `bu-kqme7f.5` spam/trash note (estimate) | 23 | 5 | 22 |

Where Butlers already outreaches Dots, and this run builds on it: the house (Home Assistant), the phone (Telegram, OwnTracks), money, health records and owner-held memory. Measurements that shape the moves:

| Measure | Value |
|---|---|
| Scheduled model work today | 12 prompt schedules (about 6.9 runs a day) against 87 deterministic job schedules |
| Lead goal cost | about $0.079 on Sol, $0.0043 on Luna or Haiku 5.5 |
| Small-first rung for third-party goals | about 65% saved (about $61 a month at 40 goals a day) |
| Monthly ceiling today | fail-open, halts owner turns too, no successor after the switch |
| Telegram bot surface | 9 Bot API methods; url buttons pass unchecked |
| LLM-presentable tools | 1,133 |
| Standing rules in use | 0 at the 2026-07-05 audit |

## Ranked moves

Trust prerequisites lead (every exposed-goal feature ends at the approval card, a live link or the spend ceiling); then owner capabilities by value and dependency. Cost is S/M/L.

### 1. Cost governance across the RFC 0038 switch: a fail-closed monthly fleet pool with an owner reserve, plus origin-aware starting rungs

`bu-jv1moa.1` · Trust prerequisite · cost L. Today's monthly ceiling lives only in the spawner (spawner.py:2172-2215; check_monthly_ceiling in model_routing.py ~3080), fails open, and halts owner turns too. RFC 0038 deletes the spawner and names no successor, which three lenses found independently, so after the switch spend is bounded only per goal. Dispatch intent derives from role only, so inbound chatter runs on the same rung as the owner's own turns. **Payoff:** With a $100 ceiling, background stops at $80 and the owner keeps $20 (about 250 owner goals at $0.079); /status shows 'obligation lane 78% of $40, owner reserve intact'. A small first rung for third-party leads saves about 65% (about $61 a month at 40 goals a day; breakeven escalation rate 94%).

### 2. Exact-call approval card: the phone card shows the stored recipients and content, labels model text as claims, binds the args digest, and offers Approve only when the whole call fits

`bu-jv1moa.2` · Trust prerequisite · cost M. Today's card shows no recipient or content, and its only explanation fields are model-written (src/butlers/modules/approvals/notifications.py:106-123), so an injected email can write everything the owner reads before tapping. emit_approval_push has no caller, and bu-ys9hdx.1 may not activate delivery until this ships. Every exposed-goal feature in this epic ends at this card. **Payoff:** The phone card reads 'Send email · To: Dana Lee <dana@x.com> · Subject: Re: Thursday · Body: (full text, links defanged) · Started by: email from dana@x.com 09:12, an outside sender · Assistant says: confirms the slot she asked for' with [Approve] [Reject] [Open]. A 9,000-character body gets [Open] only; a stale tap after the call changed executes nothing.

### 3. Owner link references: every live link or button on an owner surface comes from a server-held provenance row (a dashboard route, a page the goal's research opened, or an owner-confirmed vendor door)

`bu-jv1moa.3` · Trust prerequisite · cost M. RFC 0038 makes almost every useful goal exposed, and exposed owner notify must render URLs inert, so research sources, vendor handoffs and even the approval card's own dashboard button lose their one tap. Today's open_dashboard.dashboard_url is an unchecked string, and validate_telegram_reply_markup checks only callback_data. Three ad-hoc exceptions become one rule. **Payoff:** A research answer arrives with [Source 1 · rtings.com (page the research opened)]; a renewal warning with [Cancel · adobe.com (vendor link you confirmed 12 Oct)]; any URL the model typed shows as hxxps://... with no preview.

### 4. Read every document the owner receives: paged PDF text, page images for scans, the lazy Gmail attachment fetch, and prompt-free document deadlines classed as record

`bu-jv1moa.4` · Capability · cost M. No PDF over about 48 KB can be read today (src/butlers/tools/attachments.py:38), and no Gmail attachment is ever fetched (GmailConnector.fetch_attachment, gmail.py:3271, has no caller), so the promise to read bills and travel documents is unmet. The deadline delta catalogs every deadline_create as administer, so a bill's due date costs a tap and a third-party-origin goal can never set one. **Payoff:** An SP Group bill PDF arrives and the lead replies 'S$142.30 due 28 Oct, up 18% on September (p.1)'. With no tap, Telegram later says 'Car insurance renews 12 Nov (policy p.2); cancel by 13 Oct.' Dots reads files on demand; Butlers reads every bill as it arrives.

### 5. Finished files: Drive export to PDF, XLSX and DOCX, native Sheet charts, report homes updated in place, research deliverables recorded on the goal, and Butlers-made files attached to owner Gmail drafts

`bu-jv1moa.5` · Capability · cost L. Dots' best-rated output was finished files, while Butlers' recurring reports exist only as Telegram text; bu-kqme7f.1 lists PDF export and charts as non-goals and bu-kqme7f.6 defers attachments (G7). Google renders both, so no renderer comes back. **Payoff:** 'Make the weekly brief a PDF' returns the Doc and its PDF on Telegram; on the 1st the 'Money 2026' Sheet gains a month tab and a chart; 'compare three air purifiers under S$400' yields a Sheet where every cell names its source; 'invoice Acme for September' saves a Gmail draft with the PDF attached. Flips use cases J5, J33 and J42 (coverage-reach estimate).

### 6. Home safety: a deterministic leak, smoke, CO, freezer and open-door sentinel with an immediate owner alert and an honest 'house is blind' signal, plus owner-marked critical entities that always park

`bu-jv1moa.6` · Capability · cost M. Today a leak reaches the owner only if an LLM on the frozen route classifies the HA event, and after the cutover the feed path wakes nothing without a declared subscription. Any switch, including a freezer plug, is one LLM call away from off. **Payoff:** Within seconds: 'Kitchen leak sensor wet since 14:02. You are in Tokyo (trip active). [Close main water valve] [Snooze 30m]'. When HA is unreachable for 30 minutes while away: 'Home sensing offline since 13:10; leaks and doors are not being watched.' Dots cannot see the house at all.

### 7. Owner places and confirmed presence edges: one owner-edited place registry, a fused leave and enter log, coordinate-free travel legs, and place-keyed reminders

`bu-jv1moa.7` · Capability · cost M. Places are operator env config today, departure is an unrecorded signal clear (context_producers.py:420-425), and leave-by history dies with raw fixes. Without one confirmed edge log, every presence feature (sweep, leave-by, place reminders, the sentinel's away mode) re-derives presence on its own. **Payoff:** The owner names 'Gym, 150 m' from the dashboard or a Telegram pin; Chronicles shows 'Left home 08:12 (HA + GPS), arrived Office 08:47'; 'remind me to buy filters next time I'm at Ikea' fires on arrival.

### 8. Timed and immediate delivery lanes, with the departure sweep as first consumer: one Telegram card when the owner leaves home, each open item fixable by one tap

`bu-jv1moa.8` · Capability · cost L. RFC 0011's broker delivers urgent items hourly (:332) and holds expire after 24h (scheduler.py:1920-1937), so time- and presence-anchored observations arrive an hour late or never. Three lenses independently proposed the departure sweep and four proposed leave-by; all need this lane. **Payoff:** At 08:14: 'Left home 08:12. Front door: unlocked [Lock]. Garage: open [Close]. Living-room lights: on [Turn off]. Can't see: back door sensor.' A tap executes that exact receipted call; nothing is sent when everything is secure.

### 9. Pre-event cards on Telegram: a just-in-time meeting card from the prep envelope now, then leave-by timing learned from the owner's own trips

`bu-jv1moa.9` · Capability · cost L. Per-person recall reaches the owner only on the dashboard rail or in the night-before digest. Run 13 deferred leave-by because it assumed routing-API egress; learning from travel_legs removes the egress. **Payoff:** 'Lunch with Sam Lee 12:30 @ Amoy St. Last met 3 Aug. You owe: send the deck (Fri). Sam is waiting on: intro to Priya.' Later: 'Leave by 08:40 for Dentist (Novena): usually 32 min from Office (p80 of 7 trips).' Location never leaves the host.

### 10. Standing replies: owner-authored, recipient-pinned message templates with typed slots, the only standing rule an exposed goal can use

`bu-jv1moa.10` · Capability · cost M. rules.py has no template constraint type, so every in-thread reply to a non-owner from an exposed goal parks (bu-kqme7f.6): approval fatigue is built into the design, and the owner's standing-rule direction has nothing to attach to. Zero standing rules were in use at the 2026-07-05 audit. **Payoff:** The owner writes 'Confirmed, see you {date} at {time}.' for Sam, capped at 5 sends a week for 90 days; a later reply goes out at once and the owner sees 'Sent standing reply to Sam: Confirmed, see you Thu 17 Oct at 19:30. [Revoke rule]'.

### 11. Promise radar: two-way commitments from the owner's own messages and counterparties' promises, confirmed with one Telegram tap

`bu-jv1moa.11` · Capability · cost M. The commitment ledger already has other_to_owner (core/commitments.py:129) but no message-derived writer, and commitment capture covers only owner-to-butler utterances. Dots cannot see WhatsApp or Telegram. **Payoff:** 'You told Sam on WhatsApp (Tue) you would send the deck Fri. Track it?' and 'Still waiting on Ali's quote (promised by email for Mon). [Draft a nudge]'.

### 12. Owner Projects: an owner-only versioned charter with typed links, armed by one approval of an enumerated check-in bundle, with exposure-tolerant check-ins and a Telegram project card

`bu-jv1moa.12` · Capability · cost L. D9 and D10 bound every goal to one run, so a six-week move would cost 16 approval taps under the settled rule and leave no durable home for its plan, evidence or decisions. Dots sells the project as its core unit, and its known failure is opaque progress. **Payoff:** One card arms 'Move to Tiong Bahru by Dec 15 · budget S$4k': 12 check-ins, 3 wakes, close-out, a S$6 model envelope, digest a91f [Approve]. Monday 08:00: 'Move · at risk · movers not booked, 19 days left · 2 options waiting [Choose A] [Choose B]'. At close: 'Moved Dec 14; S$3.6k of S$4k' plus an endorsable lesson.

### 13. Web watches through pinned research probes: owner-approved fixed questions run by code on the governed research host, alerting on price, availability or page change

`bu-jv1moa.13` · Capability · cost M. Study rows F12 and J21 (price and listing watch, following a developing story) are gaps. A watch built as an obligation that runs a goal fails on the exposure rule; a probe whose question the owner fixed puts no goal data in front of the research model, so it can run live. **Payoff:** 'Watch the Sony WH-1000XM7 at these two shops, tell me under S$400' sends one Telegram insight with live source links when the price crosses; 'tell me if my airline's baggage policy page changes' runs daily.

### 14. Inspectable owner knowledge: an owner-only Vouch for reported, inferred or web-derived facts, and a compiled owner profile page that is exactly what every lead is told

`bu-jv1moa.14` · Capability · cost M. The cutover spec holds non-owner facts until the owner endorses them, but code endorses only rules (storage.endorse_rule, storage.py:3101), so memory grows more tainted with use. D15 puts an owner profile in every lead's prompt, yet nothing defines it, leaving the most powerful steering text uninspectable. **Payoff:** 'Sam said your passport expires 2027-03 (reported, not verified). [Vouch] [Retract] [Later]'; /me shows 'What every Butlers lead is told about you, version 37', each line linked to its fact.

### 15. Health visit brief: an owner-only 'since your last appointment' summary delivered before each medical appointment

`bu-jv1moa.15` · Capability · cost M. The Health manifesto promises an answer when the doctor asks how the owner has been, yet no bead, move or job delivers it at the appointment. The critic found health was the one lens no agent covered. **Payoff:** Before 'Dr Tan, Fri 10:00' the owner gets symptom, adherence and trend lines against baselines plus 'Questions you noted'. Dots has no wearable, medication or symptom data.

Sequencing: move 8 needs move 7's edges; move 9's leave-by slices need moves 7 and 8; move 13's links need move 3; move 12's envelopes use move 1's pool. Each move's first slice ships alone.

## Amendments the moves carry

Each lands only with the owner's approval of its exact text, which is in the move's bead.

- Move 2: one security.md sentence: Approve only where the whole stored call is shown; assistant text is labelled as its claim.
- Move 3: a render_links_inert carve-out for server-resolved link references in the core-notify and butler-messenger deltas and the D4 communicate row.
- Move 6: one RFC 0028 sentence: reversible applies only to enumerated entity targets not marked critical by the owner.
- Move 8: the RFC 0011 timed lane (also owner decision 4).
- Move 10: one security.md sentence: template-pinned rules pin every recipient and exclude free text.
- Move 12: one v1.md scope line for owner projects (also owner decision 5).
- Move 13: a D18 'pinned research probe' kind (part of the D20 questions, owner decision 3).
- Move 14: one RFC 0038 D6 sentence: the owner may endorse a held fact on an owner surface, keeping its lineage.

## Owner decisions

Real candidates that need an owner answer; none is a child of the epic.

1. Outside-service admission ladder (the 'more APIs' premise): typed connector first, then an owner-pinned outside MCP adapter that runs in the owning domain, never in a session. Needs security.md and RFC 0038 D4 amendments before any bead.
2. Vendor doors: approve the security.md clarification 'A door opens a vendor page whose host the owner confirmed; it never carries an amount, cart contents or confirmation token, so following it spends nothing.' Move 3's door slice waits on it.
3. D20 open questions on bu-6qaju2.1: citation rendering, the pinned-probe kind (move 13), and whether the owner may approve a live research call from an exposed goal.
4. RFC 0011 timed-lane amendment (move 8): delivered in its window or not at all; dnd and sleeping always hold; quiet hours yield only to a fresh presence edge.
5. v1.md scope line for Owner Projects (move 12), and whether to adopt the optional auto-wake amendment (recommended: no).
6. WhatsApp sends: whether any reply, approved or standing, may go out over the personal WhatsApp user client given ban risk (critic note on bu-kqme7f).

## Security findings filed during the run

Lenses found four live or latent defects; each was verified against code, filed under `bu-svqpzo`, and made a blocker of the reconciliation bead `bu-svqpzo.6`:

- `bu-svqpzo.9`: Refuse calendar bulk and high-impact mutations when approvals is not wired (general, health, finance, lifestyle, travel).
- `bu-svqpzo.10`: Email-derived subscription cancel links and cancellations stay unverified until the owner confirms.
- `bu-svqpzo.11`: Scrub credentials from Home Assistant events (the HA connector never calls scrub_envelope).
- `bu-svqpzo.12`: Park HA reversible calls with area, device, 'all' or data-carried targets, or unlisted data keys.

One shared prerequisite has no owner: prod enablement of approval delivery (`approval_delivery_rollout` flags). `bu-ys9hdx.1` activates in dev only and the switch release `bu-enbrs2` has no checklist item; both beads now carry a note.

## Critic's verdicts

The critic re-checked 10 high-severity code claims at file:line and found none wrong. Its corrections are in the ranked moves:

- Six capabilities appeared in several lenses (leave-by in 4; departure sweep, spend ceiling, drafting voice, links under exposure and deliverable files in 3 each). Each became one move or one follow-up.
- Three top moves existed only as notes on other beads (spend ceiling, exact-call card, standing replies). They now have owning beads (moves 1, 2, 10).
- Doctrine: the homecoming routine would actuate from tainted trip rows (excluded); vendor doors need a security.md clarification (owner decision 2); the Mini App's web_app buttons conflict with `bu-fg499t` (deferred); attachments on drafts stay parked under owner choice A (move 5).
- Missing lens: health, where Dots has no data at all. The critic's visit brief is move 15.
- No move spends money, touches Slack, builds on the frozen classify-and-route path, gives the lead web or shell access, or reopens the schedule-origin follow-up rule.

## Vetted, not ranked

Real candidates held back as later slices, direct beads or next-run material. Each is in the data file under `.synthesis.deferred`.

- Capability-gap ledger: every 'I could not' leaves a typed, content-light record.
- Monthly governed growth proposal built from the gap ledger; code lands through beads, never through the lead.
- Mail disposition ledger with mute-without-sending and an undo receipt (pairs with the bu-kqme7f.5 disposition note).
- Returns, refunds and warranty desk on the expected_refund cost-claim substrate.
- Cancel-and-verify pack with a guard against charges after cancellation; after bu-svqpzo.10.
- HA companion app phone perception (allowlisted notifications, next alarm, call state); see the bu-8cdl1.14 note.
- FX reference-rate feed; folds bu-8cdl1.5's never-filed slice S3.
- Trip destination brief from booked trips.
- Trip money: attribute charges to trips and close each trip with its cost.
- Trip pack: an offline Telegram bundle before departure.
- Telegram Mini App console; conflicts with bu-fg499t's web_app refusal and needs its own auth design.
- Live 'Now' card: one pinned Telegram message edited in place.
- Phone capture fidelity: keep pins, venues, contact cards and owner edits (bu-z0b0ah.6 note).
- Find-a-time with internal holds and a parked owner-voice reply.
- Weekly time intentions measured against lived time.
- A protected work block for each owner promise with a deadline.
- Grounding receipts: what each goal read, and a 'Why?' button.
- Distrust a source: contain a poisoned sender, page or goal across memory, reversibly.
- Network-less analysis sandbox over the goal's own read results.
- Long audio and voice-note transcription into owner goals (reshape bu-r0dsz first).
- Approval-gated Drive share to a named person, with revoke.
- Load versus recovery: tomorrow's calendar against sleep and HRV bands.
- Pay versus use: each renewal against evidence of use.
- Energy cost truth: meter kWh joined with utility bills.
- Usage-metered maintenance and battery-depletion forecast.
- Obligation preconditions: a deterministic 'anything to judge?' gate before admission.
- Obligation cost cards with one-tap demote or pause.
- Cache-stable lead prefix contract with a cached-share deadman.
- Set card: one tap over an enumerated set of parked effects (S3 of move 2).
- Rule rehearsal: zero-authority shadow rules with exposure-honest coverage.
- Drafting-voice learner (merged with lens-self-improvement#3 and lens-memory-knowledge#5): owner-confirmed style guidance from edits.
- Research library: cited answers kept as dated, tainted records.
- Public context for external organisations in meeting prep (rides move 9).
- Decision projects that hand off a buy-ready packet without buying (rides move 12's decision kind).
- Paperwork ledger: typed bill, policy and ticket records with page-cited fields (after move 4).
- Homecoming routine: excluded on doctrine, because it would actuate from tainted trip rows.

## Ledger feedback

The lenses left 121 notes on existing beads. While the run was in flight the coordinator verified and applied 32 of them as notes on 23 beads; synthesis added 49 more, for 81 notes on 61 beads. No bead's scope, status or priority was changed. Read them with `jq '.synthesis.ledger_feedback[] | {bead, note}'`. The ones that change what to do:

- `bu-kqme7f.6` now parks Gmail drafts from exposed or third-party goals (owner choice A); truncated previews drop Approve.
- `bu-fg499t` must refuse caller url, login_url and web_app buttons; `validate_telegram_reply_markup` checks only callback_data today.
- `bu-6qaju2.4`: only `urls_opened`, never model citations, may become live links; research facts carry a server-stamped research_call_id.
- `bu-ys9hdx.1` must not activate delivery before move 2's first slice; the presented args digest is bound at callback.
- `bu-o9qax4`: deterministic presence, departure and safety jobs must not become obligation admissions; obligations gain bundle_id and until_at.
- `bu-x7x1r4` and `bu-bdlr3` fold into one owner places editor (move 7); `bu-s11n0s.10` should share move 12's bundle-arming substrate.
- `bu-kqme7f` epic: nobody has asked the owner about WhatsApp send ban risk (owner decision 6).

## Method and access

- Orchestration: 21 agents in 10 batches (two per batch, three in the last), about 55 minutes apart, never more than three in flight. Deep-design and critic briefs were written from the durable harvest mid-run. Workflow run ids are in `orchestration.workflow_runs`; zero agent errors. Models: opus/high for every agent.
- Agents were read-only except for scratch files outside the tree; evidence comes from `rg`, file reads, `git log` and the Dots study. No test suite, DB or live stack was run, so costs and payoffs are estimates; each owning bead measures its own outcome.
- Access: `git show 3df72ae7c552:docs/redesigns/2026-10-15-dots-superset-pursuit-data.json | jq '.audits["<lens>"]'`; lens keys are in `orchestration.batch_plan`. `jq '.synthesis.ranked_moves[] | {rank, title, bead}'` lists the moves. Long strings in `audits` are capped for size; the harvest file was not committed.
- Retention: this PR removes `2026-10-13-simplification-pursuit-data.json` from the tree per the two-newest-runs rule; read it with `git show b747a55a530364d987fb37457c343193e9362fe7:docs/redesigns/2026-10-13-simplification-pursuit-data.json`.
