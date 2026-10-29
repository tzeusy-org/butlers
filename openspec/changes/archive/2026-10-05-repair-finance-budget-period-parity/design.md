## Context

See proposal.md for the installed-schema failure. The canonical packet for bu-aubi0k resolves period parity before code. finance_006 is historical; the current finance head is finance_015. RFC 0012 requires quarterly and calls its annual span annual, while shipped persistence/tools/alerts use yearly. Legacy daily is already CHECK-admissible and cannot be removed without risking existing data.

## Goals / Non-Goals

**Goals:** Repair current period admission, manage existing daily rows, preserve all old rows and existing alert identities, and make downgrade truthfully non-destructive.

**Non-Goals:** No annual alias or row rewrite; no rolling/fiscal periods, forecast-policy repair, financial/provider authority, live data, new API/dashboard feature, core revision or family consolidation. bu-lsxqb0.18 and .21 retain their source gates. core_259 belongs to PR 4366.

## Decisions

1. Store daily/weekly/monthly/quarterly/yearly. Quarterly follows accepted RFC/capability requirements. Retaining daily/yearly follows actual installed-schema compatibility. Clarify the RFC's annual spelling explicitly as yearly with Jan 1..Dec 31 semantics; adding an annual alias would create an unnecessary second identity and is excluded.
2. Install finance_016 after finance_015, replacing only budgets_period_check. Preserve historical finance_006 for reproducible replay. Rewriting it alone would leave installed schemas broken.
3. Daily bounds use the same owner-local date and existing half-open midnight windows. Add YYYY-MM-DD scope to the existing alert token helper, preserving all four existing strings. A fixed 24-hour duration would miscount DST days.
4. Downgrade locks budgets before inspecting all quarterly rows. Refuse if any remain; otherwise restore the exact old CHECK. Silent conversion, deletion or deactivation would lose user intent/history and cannot satisfy the old CHECK safely.
5. Acceptance tests replay real core + finance migrations because finance_015 references public.cost_claims; seeded categories prevent a category FK failure from masking the period failure. Existing lightweight fixtures receive only enum parity updates, and never stand in for migrated-schema proof.

## Risks / Trade-offs

- Replacing a CHECK takes a brief table lock; upgrade changes no rows, and downgrade holds the same table lock across its refusal check.
- Local Docker access is denied. Record setup failure honestly, obtain real-Postgres code-head outcomes from hosted CI, and leave DB verification tasks unchecked until those outcomes exist.
- Complete MODIFIED blocks preserve all baseline scenario names and unaffected clauses. Inspect parsed deltas, validate the named change strictly, run overwrite/countable-task guards, and preserve any unrelated global strict findings without trace backfill or ratchet changes.

## Migration Plan

Ship forward migration with the compatible tools. Existing daily/yearly rows retain all fields. Downgrade is available only when no active or inactive quarterly row remains; it never changes rows to make rollback possible. Run the bounded real-chain round trip and separate-connection rollback controls.

Once genuine implementation checks pass, refresh overlapping source blocks, sync and normally archive this bounded change with validation. Verify baseline body/scenario parity and archived-requirement landing, then push the archive tail and use the required exact-head/merge-group gates. No --skip-specs, --no-validate, hand-moved archive or bare validate command counts as proof.
