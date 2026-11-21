# JARVIS pursuit, run 26: the calendar ecosystem (2026-11-22)

**Reader:** the owner deciding which calendar moves to release. **Status:** released by the owner on 2026-11-22; nothing below is built yet. **Epic:** `bu-5ii4gj`. **Release gate:** `bu-5gywb6`, closed by the owner's sign-off on 2026-11-22. Moves 7, 12 and 14 still wait on named prerequisites. **Report:** https://claude.ai/artifact/CMbkGQPx9EeQy4tyf2Ccea

## Direction

Make the calendar know whose time it is, say only what it can prove, and finish the scheduling work it currently only detects. Six trust repairs lead because each one is a place where the system states something about the owner's time that it cannot back. Nine capability moves follow. Almost all of them are deterministic: they need typed records, a reconciler and an approval, not another model session.

The north star is unchanged: [earned calm and absorbed mental labor](pursuit-ledger.md#north-star).

## Scope and evidence

- Lens: everything the system perceives, knows, decides, does and says about the owner's time. Non-standard run: calendar surfaces only, no fleet-wide tier board.
- Source baseline `6644063490b02f2c1409cc113f2f16e19b3ee83c`. Evidence is static and source-confirmed. No live calendar, Google account or deployed stack was inspected.
- Eleven read-only agents in four hourly batches, never more than three in flight: eight capability lenses (perception, scheduling action, proactivity, cross-butler, time knowledge, inference flow, owner-absent autonomy, one deep design) and three surface audits.
- They returned 57 candidate moves and 70 findings (2 critical, 34 major). Fifteen moves are ranked here; 18 candidates are dropped or deferred below. Every agent checked novelty against the pursuit ledger, the ideas ledger, `openspec/` and Beads.
- Runs 24 (Chronicler regeneration, epic `bu-uhbhzq`) and 25 (QA self-healing, epic `bu-ejow5p`) were filed the same weekend. Moves 3, 10 and 14 name their dependency on the Chronicler run.
- The owner's 2026-10-21 decision (`bu-kf3n4i`) stands: `/calendar` is an operator view, and Google writes stay MCP tools and approved proposals. No move restores the dashboard's own edit dialogs.
- Per-agent structured output stays in a gitignored local harvest file. The durable record is this dossier plus the structured `design` and `acceptance` fields of each bead.

## Ranked moves

Trust and correctness defects lead. Value is a planning judgment on a five-point scale; cost is comparative.

| Rank | Move | Kind | Value / cost | Bead |
|---|---|---|---|---|
| 1 | Whose time is this | Trust repair | 5 / M | `bu-5ii4gj.1` |
| 2 | Owner day sheet | Trust repair | 5 / M | `bu-5ii4gj.2` |
| 3 | People in time | Trust repair | 5 / L | `bu-5ii4gj.3` |
| 4 | Calendar write and render truth | Trust repair | 4 / M | `bu-5ii4gj.4` |
| 5 | Zone-true time | Trust repair | 4 / M | `bu-5ii4gj.5` |
| 6 | Organizer verdict and invitation quarantine | Trust repair | 5 / M | `bu-5ii4gj.6` |
| 7 | Time claims from mail | New capability | 5 / L | `bu-5ii4gj.7` |
| 8 | Asked for your time | New capability | 5 / L | `bu-5ii4gj.8` |
| 9 | Invitation docket | New capability | 5 / L | `bu-5ii4gj.9` |
| 10 | Changed since you said yes | New capability | 5 / M | `bu-5ii4gj.10` |
| 11 | Resolve in place | New capability | 5 / L | `bu-5ii4gj.11` |
| 12 | Protected and elastic time | New capability | 4 / L | `bu-5ii4gj.12` |
| 13 | Domain time claims | New capability | 5 / L | `bu-5ii4gj.13` |
| 14 | Planned versus lived | New capability | 4 / L | `bu-5ii4gj.14` |
| 15 | A calendar you can ask and point at | Surface | 4 / L | `bu-5ii4gj.15` |

1. **Whose time is this.** Calendar discovery registers every calendar the Google account can see (a partner's shared calendar, colleagues, team calendars, holidays, birthdays) as lane `user` (`src/butlers/modules/calendar/__init__.py:583`), and no reader asks which calendar a row came from. `is_owner_attending` returns true when no attendee is `self` (`src/butlers/core/temporal/calendar_provenance.py:44`), which is exactly an event on someone else's calendar. Another person's appointment can therefore set the owner's `meeting` context, count toward day load and conflicts, remove a free slot, and prompt a debrief. Give each source a role (owner, household member, other person, team, subscription, holidays, birthdays, butler) with a stated basis, and route every reader through one owner-time predicate. A partner's calendar then becomes honest household context instead of fabricated owner state. This is also the precondition for every further time source. Depends on or sits beside: Adjacent, not duplicate: bu-s11n0s.4 (attendance and typed events), bu-ep60gq.6 (connector-owned mirror; carries the role column once it lands). Prerequisite for moves 7 and 12.

2. **Owner day sheet.** Two surfaces can tell the owner tomorrow is clear when the system cannot know. The dashboard day briefing renders 'Tomorrow is clear' for a failed read (`frontend/src/components/calendar/DayBriefingCard.tsx:157`; the route is fail-open, `src/butlers/api/routers/calendar_workspace.py:1132`). The 23:00 Telegram tomorrow-prep is a model listing one provider calendar with no RSVP filter, hard-coded SGT, a mismatched argument example, and an auth error that reads as 'No events scheduled for tomorrow' (`roster/general/butler.toml:35`). Replace both with one deterministic day sheet: next commitment, committed hours, longest free window, what needs the owner, each clause a door with a freshness stamp, and three distinct states for empty, stale and unknown. The same projection feeds the `/calendar` opener, the nightly Telegram message, and a 'next, free until' line on the existing `/now` card. Depends on or sits beside: Consumes move 1's owner-time predicate when present; ships without it using today's attendance rules. Adjacent: bu-vpn5ij (occurrences and day load), bu-dhloi1.21 (/now card).

3. **People in time.** Google-synced meetings never get attendee-to-entity links, so the prep rail, the T-20 card, Chronicler participants and linked people are silently empty for real meetings; the link exists only for butler-created events. Where history is written, it is written from invitations: 'last met' can come from a declined or never-held meeting or a native reminder, and `interaction_sync` records a mutual interaction for every invited address. Resolve attendees deterministically once per occurrence (resolved or explicitly unresolved, re-resolved when the graph changes), then keep an encounter ledger that separates planned, moved, declined, happened and unknown. 'Last met', cadence and series memory derive only from happened. Depends on or sits beside: Adjacent: bu-uhbhzq.5 (run 24 content-blind party ledger at ingestion): reuse its party identity for attendee addresses if it lands first. bu-bb9gz (meeting-prep commitment context) is a consumer.

4. **Calendar write and render truth.** A batch of source-confirmed places where the calendar reports success or calm it cannot back. A no-park replan move and `calendar_create_event` check overlap only on the Butlers calendar, so a block can land on a primary-calendar meeting and report no conflict. A failed delete of a disabled task's Google mirror is swallowed and the link nulled, leaving a ghost event; butler-authored scheduled-task events are never mirrored and the failure logs at DEBUG. The workspace silently drops repeated-title events and stops after 20 pages; multi-day events show only on their start day and overlapping events stack. The proposals lane and the overlay lane render a failed read as nothing to review. `calendar_set_primary` changes a fleet-shared key but only one butler's in-memory default, and a standing rule can be created for `calendar_respond` that shows active and never applies. Depends on or sits beside: bu-h40h2b.6 and bu-vpn5ij own recurrence expansion and day load; this batch does not touch them. bu-qozovd.1 (one mutation runner) is the natural home for the cross-calendar check if it lands first.

5. **Zone-true time.** The meeting card and calendar prep hard-code UTC+8 for rendered times and day windows, against RFC 0038's no-default-zone rule. Spaced-repetition reviews fire at the UTC wall-clock hour read in the owner zone, eight hours early for an SGT owner. The workspace overwrites an event's authoring zone with the display zone. The owner clock knows only the present, so day load, quiet hours and prep for a trip day are computed in the home zone. Fix the defects, keep the event-local zone and show it beside the owner's, project the owner's zone for future days from confirmed itinerary, and give every recurring obligation a typed anchor (owner-local, fixed zone, counterparty zone, interval) so a trip re-anchors it once with one owner card. Depends on or sits beside: Consumes bu-dhloi1.21 (owner clock) and Travel itinerary observations. Adjacent: bu-idc5mx.9 (relationship UTC date window), bu-okh0fw.2 (trip disruption review).

6. **Organizer verdict and invitation quarantine.** Invitations are an injection and spam channel that today bypasses the owner's own blocks: an ingestion block on an organizer stops only the Switchboard envelope, while the calendar projection, invitation inbox, day load and conflict radar still show the event. Compute one deterministic verdict per organizer (owner, known and verified contact, known unverified, unknown, blocked) and have every calendar reader honor it. Blocked and unknown-organizer invitations are quarantined: they hold no time, reach no prompt as text, and wait in one review list with exact local verbs. Every effect is local, so the whole mechanism can be pre-authorized. Depends on or sits beside: Consumes move 3's participant resolution when present; ships with address-level verdicts first. Consumes the owner law layer (bu-dhloi1.10) when it exists; does not wait for it.

7. **Time claims from mail.** Tickets, restaurant, clinic and hotel bookings and non-Google invitations arrive as mail, often with a machine-readable `.ics` part or a schema.org reservation. Today the part is fetched, a seed rule routes every such mail to Relationship (`roster/switchboard/migrations/003_switchboard_routing.py:159`), and nothing parses it, although `docs/connectors/attachment-handling.md:37` says it is parsed and forwarded to the calendar. Add a deterministic parser and a reconciler that states one of four verdicts per claim: on the calendar, missing, moved, cancelled. The owner gets 'Your Saturday 20:00 ticket is not on your calendar. Add?' and 'The restaurant moved your booking to 20:00; your calendar says 19:30', each with the source mail as its door. Depends on or sits beside: Uses move 1's roles to decide what 'on the calendar' means. Adjacent: bu-jv1moa.56 (attachment fetch check), bu-krc9su (declared sources).

8. **Asked for your time.** Scheduling with another person is the most frequent time errand that involves someone else, and no loop closes it. The free/busy finder is read-only and returns back-to-back slots, proposals carry one inferred event, and nothing records that a slot was promised. A time-ask ledger records each ask, offers three well-spread slots, holds them privately as owner-only blocks, gives the owner one card with the exact reply to approve, limits the counterparty's answer to the offered slots, books the chosen one and releases the rest, including on expiry. The outbound form ('find a time with Sarah') runs as one whole-goal lead that waits on the RSVP to its own invite and settles deterministically on accept. Depends on or sits beside: The outbound slice needs the goal runtime's waiting state (bu-dhloi1.7); the inbound ledger and holds do not. Uses move 12's hold primitive if it lands first.

9. **Invitation docket.** Standing RSVP judgment ('decline anything in my focus blocks', 'accept recurring 1:1s from my manager', 'tentative for all-hands') is the largest piece of recurring calendar labor. Owner decisions forbid sending without a per-event approval, but RFC 0038 D13 counts one 'approve all' on a decision set that shows every exact call as that approval. Typed owner-written policies therefore do all the thinking: each pending invitation gets a prepared answer with the policy that produced it, grouped into a docket the owner signs once. The same evaluation flags invitations outside the owner's declared meeting hours and days before the owner accepts. Depends on or sits beside: Needs decision sets (bu-dhloi1.7, second cutover) for the one-tap set; the prepared answers and the declared-hours flag ship before it as per-event approvals. Uses move 6's organizer verdict as a policy input.

10. **Changed since you said yes.** The commonest way time silently stops being true is an organizer moving, relocating or re-membering a meeting the owner already accepted. The projection overwrites in place and nothing compares the current row with what the owner agreed to. Record what the owner accepted, detect a material difference, and send one card ('Thursday review moved 14:00 to 16:30, now overlaps dentist'), struck through in place if the change reverts. The same anchor makes reminders truthful: 'an hour before the dentist' is stored relative to the event, rebased when it moves and retired when it is cancelled or declined. An owner's own drag or delete of a Butlers-owned event in Google is adopted as an instruction with a receipt and an undo, instead of being silently reverted. Depends on or sits beside: Reads bu-uhbhzq.1's revision journal when present (run 24); otherwise keeps its own accepted-snapshot. Adjacent: bu-5ro5fa (cancelled reminder mirrors).

11. **Resolve in place.** The radar detects overlaps but cannot resolve one: its only door is a create-only proposal that nothing produces, and dismiss is a session-local hide. Give each overlap two to five concrete, authority-classed resolutions compiled deterministically (move the block I own, propose a new time, decline with the exact call, acknowledge the double-booking as a premise that silences the radar until either event changes). Real days also change in batches: 'I'm sick, clear tomorrow', 'push everything after lunch 45 minutes'. A plan compiler turns one owner verb into a multi-event plan, applies it with per-step provider receipts, and leaves one composite undo. On `/calendar` the invitations, conflicts, proposals, debriefs and changed events become one keyboard 'needs you' queue. Depends on or sits beside: Builds on replan and replan undo. bu-qozovd.1 (one mutation runner) is the natural executor. Decision sets (bu-dhloi1.7) improve the multi-approval step but are not required.

12. **Protected and elastic time.** Owner no-meeting blocks only filter Butlers' own suggestions, and every Butlers-made block lives on a subcalendar other people's schedulers never see, so the owner's focus is undefended where scheduling happens. Three owner-only mechanisms fix that without Butlers answering anyone: native focusTime and outOfOffice blocks the owner approves once and Google auto-declines against; hold policies (focus quota, prep before external meetings) that place busy blocks and yield deterministically when a real meeting lands; and travel buffers derived from the owner's own legs, linked to their anchor event and moved with it. A displaced flexible block re-places itself inside its declared window and escalates only when it cannot. Depends on or sits beside: Consumes Absence Plans and the owner law layer (bu-dhloi1.10) as policy sources when they exist. bu-jv1moa.9 supplies leave-by legs. Move 4's cross-calendar conflict check is a prerequisite for safe placement.

13. **Domain time claims.** A bill, flight, clinic visit, review or follow-up reaches the calendar either through a read-only overlay with no freshness, or through a model-written `BUTLER:` event that nothing reconciles. The result is stale reminders after a bill is settled or a flight rebooked, duplicates between the overlay and written events, and no door from a calendar item back to the record that justifies it. One typed claim per domain record, reconciled by the record's owning butler in the same deterministic job that changes the record, makes the calendar a truthful projection of what the specialists know. The overlay gains a freshness stamp and an error state. Depends on or sits beside: Adjacent: bu-rx0tyg (health appointment facts have no write path; becomes the first Health claim), bu-okh0fw.2 (trip disruption review reads claims), bu-idc5mx.4 (prompt schedules into jobs).

14. **Planned versus lived.** Chronicler adjudicates planned blocks every day and keeps no durable answer. Persist one outcome per occurrence (happened, contradicted, attested, unknown) with observed start and end. From corroborated outcomes derive n-of-m norms ('deep work: planned 2h, held 1h10 median, 9 of 14'), shown beside declared preferences and adopted only by an owner click. Add owner-declared weekly targets per life lane, with a Monday check that the week's free time can hold them and a Sunday comparison against lived time. Depends on or sits beside: Sequence after run 24's Chronicler programme (epic bu-uhbhzq: derivation ledger .6, regenerable day .12, owner assertions .13): outcomes must be derived rows under that lineage, not a parallel store. Consumes move 3's encounters.

15. **A calendar you can ask and point at.** The workspace is an honest operator view that cannot answer a question about time. Search is trigram-on-text, the free-slot finder exists only for agents, an event cannot be addressed by URL (selection lives in component state), so the Telegram meeting card, project links, approvals and Chronicles can only point at the bare page, and the global Finder cannot find an event. Add deterministic time questions in the palette ('when am I free for 45 minutes', 'what do I have with Sarah') answered from the mirror with ghost blocks; a model-free quick-add compiler that shows the exact local time and any clash before Enter; one minted occurrence reference honored by `/calendar` and adopted by every off-page mention; calendar occurrences in the global Finder; and keys for view, range, date and entry walking. Depends on or sits beside: bu-q11586.9 (Page[T] conversion) touches the same routes. The RFC D18 versus one-mind contract disagreement on quick-add is settled by this move's compiler.

## Supporting tier board

Calendar cohorts only. The two new cohorts have no baseline, so no movement is claimed for them.

| Surface cohort | Verdict | Movement | Basis |
|---|---|---|---|
| calendar (workspace core) | functional | unchanged from functional on 2026-10-06 | Honest operator view with good degraded notes; cannot show spans, overlaps or zones truthfully and has almost no keyboard surface. |
| calendar decision surfaces | functional | new cohort | Detection is good (deadman, radar, invitations, prep, debrief). Resolution is thin and the day briefing can fabricate calm. |
| calendar across other surfaces | weak | new cohort | Owner time is absent from the Overview, the shell and the Finder; off-page mentions are dead ends or raw ISO text. |

## Themes

- **Time rows carry no provenance.** A calendar row does not know whose it is, which zone it was written in, how fresh it is, or which domain record justifies it. Every reader then treats it as the owner's own, current, home-zone time. Moves 1, 5, 13.
- **Failed reads render as calm.** The day briefing, the nightly Telegram prep, the proposals lane and the overlay lane all turn an error into 'clear' or 'nothing to review'. Moves 2, 4.
- **History is written from intent.** 'Last met', interactions and Chronicler's planned blocks record that something was scheduled, not that it happened. Moves 3, 14.
- **Detection stops at a warning.** Conflicts, invitations and requests for the owner's time are perceived well and then handed back to the owner as the integration layer. Moves 8, 9, 11.
- **Models sit on arithmetic paths.** Tomorrow-prep, quick-add and 'nothing agreed' each open a model session for work that is a query or a parse. Moves 2, 15.
- **The calendar stays on its page.** An event has no address, the Finder cannot find one, and no other surface shows what is next. Moves 2, 15.

## Dropped or deferred

- Day facts: Day facts (public holidays, observances, school terms with provenance): strong, parked for the next run; needs move 1's roles first.
- Forecast at the event's place: Forecast at the event's place: low value until attended places resolve reliably.
- Owner-authored task due dates (Google Tasks) as a read-only time source that feeds day load and 'due today': Google Tasks due dates as a read-only source: value 2.
- Owed before you meet: Owed before you meet: belongs to the open meeting-prep commitment epic bu-bb9gz; noted there, not refiled.
- Join-or-go readiness: Join-or-go readiness card: value 3, waits on conference-data capture.
- Charter the owner's forward time: Charter for forward time pushes: governance, folded into each move's premise clause.
- Errand placement: Errand placement into free time: depends on moves 12 and 13.
- Trip-aware reunions: Trip-aware reunions: depends on moves 3 and 5.
- Gathering at home: Gathering at home card: value 3.
- One-tap debrief: One-tap debrief sink: small; fits the open add-meeting-debrief change.
- One calendar capability domain for the goal catalog: One calendar capability domain for the goal catalog: belongs to the RFC 0038 cutover programme.
- Exactly-once calendar effects under the broker: Exactly-once calendar effects under the broker: belongs to the RFC 0038 cutover programme.
- Calendar exposure posture: Calendar exposure posture (who can see and write the owner's time): value 3, revisit with move 1.
- Load versus recovery: Load versus recovery evening card: depends on move 2's day sheet.
- Meeting yield ledger: Meeting yield ledger per recurring series: depends on moves 3 and 14.
- One calendar read-model key namespace with enforced bus coverage for every off-page calendar reader: One calendar read-model key namespace with bus coverage: fold into move 15's implementation.
- Spoken time: Spoken time accessible-name builder: fold into move 4's grid work.
- Now-anchored grid and live day read: Now-anchored grid: covered by move 2's day sheet line.

## Release

The owner signed off on 2026-11-22 and gate `bu-5gywb6` is closed. Before release every bead received a complexity label and a governing `spec_id`, and the real prerequisites were wired: moves 7 and 12 wait on move 1, move 12 also waits on move 4, and move 14 waits on the Chronicler derivation ledger (`bu-uhbhzq.6`). `bd lint` reports zero warnings. Every move starts with an OpenSpec change before runtime code.
