# Dashboard Chronicles — Spec delta for chronicler-intent-evidence-activity

## MODIFIED Requirements

### Requirement: Category Taxonomy Mapping

The "where the time went" surface SHALL render the Activity lane taxonomy
(`sleep`, `exercise`, `work`, `play`, `social`, `travel`, `eat`, `rest`) rather
than source-shaped categories. The pie chart SHALL be removed; lane time is
presented via the Day Ribbon and balance rings. No "calendar" lane is rendered.

#### Scenario: Lanes rendered, pie removed

- **WHEN** the chronicles page renders the time surface for a day
- **THEN** Activity lanes are shown via the Day Ribbon and balance rings
- **AND** no pie chart and no "calendar" lane are rendered

## ADDED Requirements

### Requirement: Day Ribbon With Ghost Intent Track

The day view SHALL render a horizontal timeline of Activity lanes with a faint
"ghost" Intent track showing planned (calendar) blocks above the lived
activities, so plan-vs-reality is visible. Each activity block SHALL be
clickable to reveal its evidence chain.

#### Scenario: Ribbon shows lived activities and ghost intent

- **WHEN** a day has both activities and calendar intent
- **THEN** lived activities render in their lanes and calendar intent renders as
  a faint ghost track
- **AND** clicking an activity reveals its evidence chain ("why?")

### Requirement: Balance Rings Vs Usual

The day view SHALL render per-lane balance (e.g. sleep, exercise, work, play,
social) annotated against the owner's usual baseline.

#### Scenario: Rings annotate deltas vs usual

- **WHEN** the day view renders balance
- **THEN** each lane shows the day's total and a signed delta vs usual
- **AND** lanes count only the Activity layer

### Requirement: Who-You-Were-With Panel

The day view SHALL render the resolved people the owner spent time with, with
co-present time and channel.

#### Scenario: Companions listed with time and channel

- **WHEN** the day has resolved social activity
- **THEN** companions are listed with co-present duration and channel
- **AND** an unresolved participant renders as unattributed rather than omitted

### Requirement: Where-You-Went Map Trail

The day view SHALL render the day's movement as a map trail, subject to the
existing map privacy contract.

#### Scenario: Movement rendered as a trail

- **WHEN** the day has movement evidence and the map privacy contract permits
- **THEN** a trail of the day's locations is rendered

### Requirement: Low-Confidence Correction Prompts

The day view SHALL surface low-confidence activities as gentle correction
prompts ("best guess: errands — correct?") that write via the existing
corrections overlay.

#### Scenario: Correction prompt confirms or relabels

- **WHEN** a low-confidence activity is shown as a prompt
- **THEN** the owner can confirm or relabel it
- **AND** the choice writes a non-destructive correction overlay

### Requirement: Zoom-Out Trends Lens

The page SHALL offer a week/month zoom-out lens showing balance trends, streaks,
and social cadence.

#### Scenario: Week lens shows trends and streaks

- **WHEN** the owner switches to the week lens
- **THEN** per-lane balance trends and notable streaks are shown
- **AND** social cadence (e.g. companions gone quiet) is surfaced

## Source References

- `dashboard-chronicles/spec.md` §5.3 (Page-Level Invariants — reads only from
  `chronicler.*`; no new client-side LLM), §5.7 (Category Taxonomy Mapping),
  §5.9 (Map Render Privacy Contract).
- `chronicler-api` delta (balance, trends, who-you-were-with, evidence chain,
  correction prompts).
- RFC 0014 (Chronicler Time Butler).
