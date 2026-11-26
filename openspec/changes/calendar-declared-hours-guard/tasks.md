## 1. Spec

- [ ] 1.1 This change reviewed before runtime code.

## 2. Slices

- [ ] 2.1 `SchedulingPreferences.outside_reason` (the reason behind `allows`) and the pure `outside_declared_hours` detector in `core/temporal/conflicts.py`.
- [ ] 2.2 The radar read model reads the declared hours once, builds the candidate inputs from provenance, and reports `hours_available`; the API issue carries `reasons`.
- [ ] 2.3 The overcommitment producer qualifies the warning issues and skips a tick whose hours could not be read.
- [ ] 2.4 Frontend: the banner and the invitation flag name the kind in words.
