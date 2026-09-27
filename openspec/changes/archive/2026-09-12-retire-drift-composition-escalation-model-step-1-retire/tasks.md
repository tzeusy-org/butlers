## 1. Retire the drift-composition escalation model

- [x] 1.1 Archive this change so `QA Escalation After Sustained Drift` leaves
      the baseline.
- [x] 1.2 Archive `retire-drift-composition-escalation-model-step-2-restore`
      immediately afterwards. Leaving the baseline without the requirement is
      not a valid resting state: any unarchived `## MODIFIED` block naming it
      validates clean and then hard-aborts at archive with
      `MODIFIED failed for header ... - not found`.
