## 1. Restore the requirement under the corrected heading

- [x] 1.1 Archive this change immediately after
      `rename-fleet-halt-scenario-heading-step-1-retire`, never before: an
      `## ADDED` block naming a requirement the baseline still carries
      validates clean and aborts at archive with
      `ADDED failed for header ... - already exists`.
- [x] 1.2 Rebuild every unarchived `## MODIFIED` block for this requirement
      against the refreshed baseline. They now omit a scenario name the
      baseline carries and fail `openspec validate --strict`.
