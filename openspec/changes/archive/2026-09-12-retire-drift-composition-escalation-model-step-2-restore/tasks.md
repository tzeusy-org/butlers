## 1. Restore the requirement under a single escalation model

- [x] 1.1 Archive this change immediately after
      `retire-drift-composition-escalation-model-step-1-retire`, never before:
      an `## ADDED` block naming a requirement the baseline still carries
      validates clean and aborts at archive with
      `ADDED failed for header ... - already exists`.
- [x] 1.2 Drop `define-infrastructure-reliability-lifecycle`'s `## MODIFIED`
      block for this requirement, which this change supersedes. Once that is
      done no unarchived `## MODIFIED` block names the requirement, so
      `define-infrastructure-reliability-lifecycle` may archive before or after
      this pair.
