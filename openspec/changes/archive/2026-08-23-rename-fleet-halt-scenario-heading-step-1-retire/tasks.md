## 1. Retire the requirement for rename

- [x] 1.1 Archive this change so `Fleet-Halt Visibility` leaves the baseline.
- [x] 1.2 Archive `rename-fleet-halt-scenario-heading-step-2-restore`
      immediately afterwards. Leaving the baseline without the requirement is
      not a valid resting state: any unarchived `## MODIFIED` block naming it
      validates clean and then hard-aborts at archive with
      `MODIFIED failed for header ... - not found`.
