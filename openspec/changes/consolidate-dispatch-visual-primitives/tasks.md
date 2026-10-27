## 1. Specification

- [x] 1.1 Add the Dispatch surface primitive requirement and scenarios.
- [x] 1.2 Define Section as the default, Tile's narrow independent-state role, quiet behavior,
      and Card retirement with no alias.
- [x] 1.3 Run strict OpenSpec and overwrite-safety validation.

## 2. Primitives and migration

- [x] 2.1 Add semantic Section and Tile primitives with Eyebrow anatomy.
- [x] 2.2 Migrate all production `ui/card` consumers and delete `ui/card.tsx`.
- [x] 2.3 Preserve each consumer's loading, error, empty, and retry branches.

## 3. State and lint discipline

- [x] 3.1 Route connector, Google Health, and topology state signals through StateDot or
      `stateColorVar`.
- [x] 3.2 Reject retired Card imports and literal CSS-token fallbacks with narrow ESLint selectors.
- [x] 3.3 Remove all production literal `var(--token, fallback)` references.

## 4. Verification and handoff

- [x] 4.1 Extend the named visual-role, connector, Google Health, topology, and StateDot seams;
      add focused Section and Tile render tests.
- [x] 4.2 Run affected Vitest nodes/files, frontend lint, `npm run knip`, build, `make check-guards`,
      and `make test-plan BASE=origin/main`.
- [x] 4.3 Push the exact head, open PR #4231, and use terminal hosted CI for broad evidence.
- [x] 4.4 Register AA-safe operational state text mappings in the existing state role and pin
      registry, resolver, binding matrix, and active-delta parity.

### Connector operational-text follow-up

Bounded audit of every production connector file changed in PR #4231:
`ConnectorDetailView`, `ConnectorRosterRow`, `AttentionStrip`, `ConnectorDeviceBadges`,
`ReauthCallout`, `ScopeList`, and `connector-auth`. Checked text classes, inline foregrounds,
status branches, and resolver call sites against the active operational-state scenario.

- Event and incident failure labels shared a private base-red mapping. Both now resolve
  `failed`, `error`, and `replay_failed` through `stateTextColorVar('error')`; ingested text
  uses the `ok` role. Filtered and unknown labels retain their neutral presentation.
- Roster warnings and the attention count bypassed the resolver with AA-safe text tokens.
  Both now use the semantic resolver with unchanged degraded/error meanings.
- Auth/health labels and attention-item warnings already use the resolver. Detail liveness
  and state, device liveness, reauth callouts, and scope verdicts use StateDot with neutral
  adjacent copy; no additional colored-text mapping remains in this bounded set.
- Before implementation, all four failure-label assertions failed on the previous code
  (three event statuses and a failed incident). Updated roster-warning and attention-count
  assertions also failed before their migration. Existing behavior assertions remain in place.
- The adjacent ingestion diff was also checked: `BatchSettingsCard` only changes its
  container; `EventDrawer` still used base-red error text and base-amber filter/truncation
  text. Those three foregrounds now use the shared resolver, with three assertions that
  failed on the old foregrounds. Its copy-success icon is not operational-state text.

### Seven-blocker visual correction (2026-09-28)

The independent scope ruling at pushed head `2b839a0cf1b980bd5fd927772b19193a08d7b2e1`
bounds this correction to the seven A/B findings below. Its C baseline styling debts and D
inherited Secrets Spine specification conflict remain outside this change.

| Finding | Correction and regression seam |
| --- | --- |
| Device copy | Opaque neutral identity and fresh/stale timing copy; retained device dots and timestamps. `ConnectorsRoster.test.tsx` rejects opacity reductions. |
| Google Health detail | One account mark, neutral alert explanation, shared backend-to-role adapter. All four account states with/without failure reason are exercised. |
| Roster auth column | Health guidance stays neutral beside the health dot; independent auth failures and AttentionStrip retain their own signals and recovery controls. Tests inspect text, border, and fill. |
| Passport Google Health | Shared account adapter, neutral not-configured state, one expiry mark with neutral copy and re-consent link. Existing scope visibility and expiry threshold tests remain. |
| Passport connector drawers | OwnTracks, Spotify, and WhatsApp use typed domain-to-role adapters and named StateDots. Disconnected remains distinct from not-configured. Spotify recovery and WhatsApp pairing copy stay neutral. |
| Topology | Border pattern carries state without hue: solid/running, dashed/overdue, double/offline, dotted/idle or unknown. Tone still takes precedence. Accessible names carry identity and state; Enter/Space opens the focused node. All nodes use the neutral page surface for border contrast. No visible state words were added to nodes. |
| Autonomy demotion | Section loses its amber fill/border. Existing explanation, badges, and confirm/dismiss actions remain. Keyboard actions are exercised. |

Focused assertions failed before correction for all seven findings. Additional falsification
caught the old pairing confirmation color and the low-contrast topology surface. Consumer tests
require the retained mark as a positive control. The Health-only private-map assertions were
replaced by rendered consumer checks; unrelated credential expectations remain.

Chromium checks used synthetic fixtures and the actual compiled CSS in both themes, including
computed opacity and background composition. Across 60 fixture/theme combinations the lowest
sampled text contrast was 4.88:1. Topology's minimum border contrast was 3.02:1 light and 5.44:1
dark; its previous light amber/deep-surface pair was 2.84:1. Re-consent focus and live ReactFlow
keyboard navigation passed. This is bounded surface evidence, not a live-data or whole-dashboard
accessibility claim. Fresh independent review and hosted CI remain subsequent gates; the PR
stays draft.

Owning verification: 954 tests passed across 51 frontend files (connectors, Passport, Google
Health, topology, autonomy banner, primitives, registry/parity, and contrast). The correction
adds 31 parameterized test cases and strengthens existing assertions; no tests were deleted.
The sole snapshot change adds Spotify's image role and contextual accessible name. The planner
reports ESCALATE for the overall PR; it executes no tests and no broad local backend gate ran.
