## Beads and adoption

Tracked specification task: `bu-jv1moa.33`. The coordinator files the implementation successors
from design.md, Successor packets, behind the owner gates there. These gates mirror deliverables;
they are not a separate execution queue.

- [ ] 1. Owner adopts RFC 0028 Amendment 1 (gate G7), verbatim or edited, into
  `about/legends-and-lore/rfcs/0028-home-physical-actuation-contract.md`.
- [ ] 2. Adopt REQ-module-home-assistant-001 to 004 and REQ-home-deterministic-jobs-013 to 015,
  matching the adopted amendment text. After `specify-home-departure-fixes-arrival` is archived, file
  the REQ-home-deterministic-jobs-011 follow-up delta (design.md, Resolver follow-up).
- [ ] 3. Record the owner's answers to G3 and G6 where they touch mitigations (design.md). Until
  answered, the safety alert is unchanged and every mitigation is dashboard-approved only.
- [ ] 4. Implement the critical registry and gate (successor S-G), then pre-call state and
  `already_satisfied` (S-H), then safety mitigations (S-I), each dark, with the test cases named in
  design.md.
- [ ] 5. Owner activation: registry entries and mitigation declarations are owner acts on the
  dashboard; the mitigation flag turns on in one owner-approved change.

Specification completion requires strict OpenSpec validation, the requirement-trace check,
independent review and linkage from docs/butlers/home.md. Tests for this planning task: +0 ~0 -0.
