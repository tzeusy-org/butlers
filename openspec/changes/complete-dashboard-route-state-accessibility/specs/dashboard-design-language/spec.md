## ADDED Requirements

### Requirement: [TARGET-STATE] Routed accessibility inventory includes real page states
Every rendered dashboard destination SHALL have accessibility evidence derived from the canonical route and capability configuration, including static and parameterized destinations. Each rendered destination SHALL have a representative loaded state, every applicable degraded and empty state, and each consequential overlay or composite interaction identified by an exact route-pattern and state identity. Redirect aliases SHALL be classified and resolve to a covered destination without being silently counted as a rendered page.

ID: REQ-dashboard-design-language-003
Source: dashboard-design-language Page Conformance and Interaction Affordances; bu-6jv4m.12; [Observed] frontend/src/lib/shell-capability.ts, frontend/src/router-config.tsx, frontend/src/test/axe/route-sweep.a11y.test.tsx
Scope: v1-mandatory

#### Scenario: A new static or parameterized route enters the inventory
- **WHEN** the router gains a navigable destination or an existing destination gains a consequential state
- **THEN** its exact route pattern and required states appear in the accessibility inventory before the change can claim complete coverage
- **AND** a parameterized route uses a synthetic, non-private identifier and a production page component in its evidence fixture

#### Scenario: Data states are distinct from loading shells
- **WHEN** a routed page has loaded, degraded, or empty behavior
- **THEN** the applicable states each have distinct accessibility evidence for the rendered production surface
- **AND** a loading-only pass does not satisfy the loaded or failure-state obligation

#### Scenario: Redirects do not hide uncovered destinations
- **WHEN** a compatibility or alias route redirects to a canonical destination
- **THEN** the inventory records the redirect relationship and verifies the reachable destination's required states
- **AND** the alias is not counted as an independent loaded-page pass

### Requirement: [TARGET-STATE] Route states remain keyboard-operable and perceivable
Required route-state evidence SHALL exercise the rendered production page with automated accessibility checks and a keyboard-only interaction path. Primary actions and consequential controls SHALL be reachable in reading order with the existing visible focus floor; overlays SHALL move focus in, support keyboard operation and Escape dismissal where dismissal is allowed, and restore focus to a meaningful trigger. Loading, completion, validation, degraded, and empty results SHALL be perceivable without relying on color alone.

ID: REQ-dashboard-design-language-004
Source: dashboard-design-language Interaction Affordances and Page Conformance; th-design accessibility WCAG 2.2 AA floor; bu-6jv4m.12
Scope: v1-mandatory

#### Scenario: Consequential menu or overlay is operated without a pointer
- **WHEN** the operator opens a consequential menu, dialog, or composite control by keyboard
- **THEN** its options and primary action are operable without a pointer, focus remains visible, and its open and selected states have accessible names and roles
- **AND** closing it returns focus to the trigger or the next meaningful control unless the confirmed action navigates away

#### Scenario: Data and failure states are announced
- **WHEN** a routed page finishes loading, reports an unavailable source, validates an input, or changes a consequential result
- **THEN** the result is available through semantic text or an appropriate status or alert announcement
- **AND** a visual color, opacity, or icon change is not the only evidence of that result

#### Scenario: First-wave complex surfaces are exercised
- **WHEN** QA's Butler menu, Memory Search, an Ingestion filter or timeline control, or a dynamic dossier is included in the inventory
- **THEN** its loaded and applicable degraded or empty state and consequential keyboard path are covered at the real component seam
- **AND** an axe-only shell or a mocked replacement page does not count as that path

### Requirement: [TARGET-STATE] Accessibility coverage debt is explicit and bounded
An accessibility gate SHALL distinguish passing route-state evidence from loading-only evidence and from uncovered or skipped states. Every temporary debt or exception SHALL identify one exact route pattern, state or interaction, reason, accountable owner, replacement evidence, and review deadline no more than 30 days after entry. A wildcard skip, expired entry, duplicate claim, or unrecorded route-state gap SHALL fail the gate. Debt SHALL NOT be represented as an accessibility pass or an exception to the WCAG contrast and keyboard floors.

ID: REQ-dashboard-design-language-005
Source: dashboard-design-language Page Conformance; craft-and-care testing-and-verification; [Observed] frontend/src/test/axe/skip-manifest.ts and route-pages.a11y.test.tsx; bu-6jv4m.12
Scope: v1-mandatory

#### Scenario: Loading-only evidence is not promoted to completion
- **WHEN** a destination passes accessibility checks only while its data request remains pending
- **THEN** the inventory labels that state loading-only debt and names the missing loaded or failure-state evidence
- **AND** the destination cannot be reported as route-state complete

#### Scenario: A temporary skip is reviewed rather than silently renewed
- **WHEN** a required route-state is temporarily skipped
- **THEN** its exact identity, reason, owner, replacement evidence, and review deadline are recorded in the gate output
- **AND** expiration, a wildcard scope, or a new route without an entry fails the gate until the evidence is added or the debt is explicitly re-reviewed

### Requirement: [TARGET-STATE] Semantic text contrast is measured after compositing
Required informational and operable text SHALL meet at least 4.5:1 contrast against the actual painted background in both supported themes after all foreground and background alpha, overlay, and nested-surface effects are composited. An opacity modifier SHALL NOT reduce semantic text below that floor. A text-opacity use may qualify for the WCAG large-text 3:1 floor only when its rendered size and weight qualify, both-theme composited measurements pass, and the exact use is recorded as a tested exception. Existing disabled-control opacity and the permitted attention-row background tint remain governed by their current Dispatch requirements; neither is a blanket exception for informational text.

ID: REQ-dashboard-design-language-006
Source: dashboard-design-language Surface Palette, Semantic Visual Role Matrix, State Color Discipline, and Interaction Affordances; th-design accessibility contrast floor; bu-6jv4m.12
Scope: v1-mandatory

#### Scenario: Alpha-muted semantic text fails the measured floor
- **WHEN** a semantic text role is rendered with an alpha modifier on a page, elevated, or nested surface
- **THEN** its composited foreground and actual background are measured in light and dark themes against the applicable WCAG text floor
- **AND** a below-floor result blocks the change even if each underlying opaque token passes independently

#### Scenario: Large-text use has a narrow measured allowance
- **WHEN** an opacity-modified text use claims the large-text 3:1 threshold
- **THEN** its rendered size is at least 24 CSS px at normal weight or 18.67 CSS px at font-weight 700 or greater, and its composited ratio reaches 3:1 on every supported surface in both themes
- **AND** the exact use and measurement are recorded; ordinary 9-11px mono labels never inherit that allowance

#### Scenario: Typed visual roles remain authoritative
- **WHEN** a low-contrast text use is repaired
- **THEN** it uses the existing semantic role family or a separately reviewed role addition rather than a Butler identity, chart, or arbitrary raw color
- **AND** the global focus boundary and disabled-control treatment keep their separately governed behavior
