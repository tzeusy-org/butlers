# dashboard-design-language

## MODIFIED Requirements

### Requirement: Interface Copy
Interface copy SHALL follow the settled voice doctrine
(`about/heart-and-soul/design-language.md` § Voice and Copy): register is technical, terse,
slightly formal, owner-direct. Past tense for events, present for state (no future tense); no
exclamation marks anywhere; no first person — the system is a third party; "the" over "your"
where it works ("The calendar is paused"; "your" stays only when contrast matters); no hedging
adverbs (currently, presently, just, simply, basically); no celebration (no "Nice work!", no
green-check moments — quiet success is the success state); no filler ("Welcome back, Tze!" is
filler); numbers exact ("2 things need you", never "a few things") without false precision
(never "2.000"). Capitalization is sentence case everywhere except proper nouns ("Sync now",
not "Sync Now"). Button labels are active verbs with no marketing language and no punctuation
("Run patrol", not "Force Patrol Now!"). Empty states follow a two-tier model
(`about/heart-and-soul/design-language.md` § Empty states): a **page-level empty state** (an
ordinary page, panel, or table with nothing to show) uses a `{Noun} + verb phrase` title, one
short visible sentence of context if needed, and a single action button. A
**Voice-surface-inline empty state** (the briefing column, the attention list when nothing needs
attention, the Next list when nothing is upcoming — the surfaces where the system is literally
speaking in sentences) is held to the stricter rule: one serif-italic sentence with no trailing
explanation and no action button, e.g. *"Nothing waiting."*

#### Scenario: Page-level empty state copy
- **WHEN** an ordinary page, panel, or table has nothing to show
- **THEN** it renders a title plus, when needed, one short visible sentence of context and at
  most one action button, with no illustration

#### Scenario: Voice-surface-inline empty state copy
- **WHEN** a Voice surface (the briefing column, the attention list, the Next list) has nothing
  to show
- **THEN** it renders a single serif-italic sentence with no explanatory paragraph, no action
  button, and no illustration

#### Scenario: No celebration
- **WHEN** the system completes work or reports a healthy state
- **THEN** the copy states the fact without exclamation, emoji, or congratulation

### Requirement: Page Conformance
A new or redesigned page SHALL be considered in the language only when all of the following hold: (1) it uses the
two-column editorial shell or a single 1280px-max column with the same gutter; (2) hierarchy is
type and rule, not card and shadow; (3) it uses the established palette with no new colors;
(4) numbers are tabular and mono; (5) butler hues appear only on letter-marks; (6) state color
appears only when state demands; (7) at most one commit button per surface; (8) every empty
state matches its tier — page-level empty states use a title plus at most one short visible
sentence of context, Voice-surface-inline empty states (briefing column, attention list, Next
list) are one serif-italic sentence — and neither tier renders an illustration; (9) headlines
are sans 500, not bold; (10) the page reads calm at 3am during an outage. When a page cannot be
made to look like the Overview, either the language is wrong or the page is — both are worth
investigating before shipping.

#### Scenario: Conformance gate before merge
- **WHEN** a dashboard page is added or redesigned
- **THEN** the change passes all ten conformance checks (the dispatch-kit review checklist operationalizes them) before merge
