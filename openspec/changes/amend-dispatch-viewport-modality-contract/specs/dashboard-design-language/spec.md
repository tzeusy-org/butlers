## ADDED Requirements

### Requirement: Viewport and Modality Contract
Every dashboard surface SHALL be designed against exactly three device bands — **desktop**
(≥1024px, fine pointer, hover available), **tablet** (768–1023px, mixed pointer), and **phone**
(<768px, coarse pointer assumed) — aligned to the `lg`/`md` breakpoints already in use across the
frontend. Pages and components branch viewport-dependent behavior on these bands rather than an
ad hoc breakpoint.

On any surface reachable under `(pointer: coarse)` or the phone band, every interactive target
(button, pill, action-arrow list row, tap target) SHALL have a minimum hit area of 44×44px, even
when its visual glyph is smaller — via padding or an invisible hit-area expansion, never by
inflating the visual element itself.

No fact essential to understanding a surface's current state SHALL be conveyed only via `:hover`
(a tooltip-only label, a hover-reveal delta, hover-only truncation reveal), since hover does not
exist on a coarse pointer. Any fact exposed on hover on desktop MUST also be reachable without
hover — visible by default, tap-to-reveal, or included in the base layout — on tablet and phone.

#### Scenario: Device band is one of the three canonical bands
- **WHEN** a page or component branches behavior by viewport width or pointer type
- **THEN** it resolves to desktop (≥1024px), tablet (768–1023px), or phone (<768px)
- **AND** it does not introduce a fourth ad hoc breakpoint

#### Scenario: Coarse-pointer touch-target floor
- **WHEN** a surface renders under `(pointer: coarse)` or below the phone breakpoint
- **THEN** every interactive target has a minimum 44×44px hit area
- **AND** a smaller visual glyph is centered inside the expanded hit area rather than being
  enlarged itself

#### Scenario: No hover-only facts
- **WHEN** a fact needed to understand a surface's current state is exposed via `:hover` on
  desktop
- **THEN** the same fact is also reachable without hover — visible, tap-revealed, or in the base
  layout — on tablet and phone
