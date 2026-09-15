# Source-Grounded Education Traceability Evidence

This table records the scoped requirement-to-test mapping for the
`source-grounded-education` change. The cited tests execute the behavior named by each requirement;
the mapping does not claim repository-wide traceability completeness.

| Requirement | Behavior-executing evidence |
| --- | --- |
| `REQ-butler-education-007` | `roster/education/tests/test_manifesto.py::test_manifesto_preserves_source_grounding_commitment_and_boundaries` reads the governing document and pins the source boundary, citation/reading-pathway promise, transparent pedagogy, and preserved exclusions. |
| `REQ-education-source-grounding-001` | `roster/education/tests/test_source_material.py::{test_register_source_persists_metadata_under_generated_uuid,test_list_source_returns_source_ids_and_preserves_metadata,test_remove_source_deletes_only_registry_key_and_leaves_dangling_refs}` executes registry create/list/remove behavior. |
| `REQ-education-source-grounding-002` | `roster/education/tests/test_pedagogy.py::TestTeachingCiteSource` executes citation persistence and provenance rules; `frontend/src/components/education/NodeDetailPanel.test.tsx` executes the user-visible provenance states. |
| `REQ-education-source-grounding-003` | `roster/education/tests/test_pedagogy.py::TestReadingPathways::{test_registered_refs_become_pathways,test_no_pathways_when_no_source_covers_the_concept}` executes the positive and absent-source pathways. |
| `REQ-module-education-curriculum-001` | `roster/education/tests/test_curriculum.py::{TestCurriculumGenerateConceptTypes,TestCurriculumGenerateSourceRefs}` executes classification, graceful omission, and source mapping. |
| `REQ-module-education-teaching-flows-006` | `roster/education/tests/test_teaching_flows.py::TestTechniqueRecording` executes concept-type technique selection, Socratic fallback, and flow-state recording; `roster/education/tests/test_pedagogy.py` pins the named principles, citations, and reading pathways. |
| `REQ-dashboard-education-api-001` | `roster/education/tests/test_api.py::TestListSourceMaterial` executes populated, empty, nullable-field, and unavailable-pool endpoint states. |
| `REQ-dashboard-education-ui-001` | `frontend/src/components/education/NodeDetailPanel.test.tsx` executes referenced, model-recalled, dangling, unchecked, concept-type, and annotation-free rendering states. |

## Archive-safety and collision record

A capability-qualified scan of every unarchived `## MODIFIED Requirements` block found eight
same-named requirement groups across the repository. Two intersect this change:

- `module-education-curriculum :: Topic decomposition into concept graph` also appears in
  `education-mind-map-lifecycle-integrity`.
- `dashboard-education-ui :: Mind map graph visualization in Curriculum tab` also appears in
  `education-mind-map-lifecycle-integrity`.

`MANIFESTO.md Content` and `Teaching Phase — Explain, Question, Evaluate` have no same-capability,
same-name collision in another unarchived change. The `MANIFESTO.md Content` and `Topic
decomposition into concept graph` blocks in this change are rebuilt from their current baseline
bodies: the manifesto paragraph, the curriculum introduction, both numbered curriculum clauses,
and every baseline scenario remain present, then only this change's source-grounding and pedagogy
clauses and scenarios are added. The numbered curriculum clauses retain their baseline text inside
distinct Markdown bullets so the trace check sees one contiguous normative block while the strict
overwrite analysis still evaluates each baseline clause independently.

The two Education changes remain separately unarchived. This rebuild does not combine the
lifecycle change's intended edits into the source-grounding delta and does not make either archive
order independent: after one change is intentionally archived, every colliding block in the other
change must be rebuilt against the refreshed baseline before that second archive. The dashboard UI
collision is recorded here but remains outside this bead's authorized body-rebuild scope.

## Scoped mechanical check

Run the repository trace checker in authoring mode with the relevant backend and co-located
frontend test roots and retain its complete output as the global baseline record. Scope the
acceptance check by the active change's spec path, not by requirement ID: structural diagnostics
name the spec path and requirement title but do not include the ID. The scoped result is clean only
when no `ERROR` or `WARN` line names any spec under
`openspec/changes/source-grounded-education/specs/`. Requirement-ID resolution is checked
separately by the repository citation guard. The repository-wide trace command is expected to
remain non-zero while unrelated baseline findings exist; its final summary must be reported
separately.

```bash
trace_log="$(mktemp /tmp/source-grounded-education-trace.XXXXXX.log)"
uv run /home/tze/.dotfiles/ai-bootstrap/skills/personal/th-projects/scripts/spec-trace-check.py \
  "$PWD" --tests-dir tests --tests-dir roster/education/tests --tests-dir frontend/src \
  --authoring >"$trace_log" 2>&1
trace_rc=$?
rg -n 'openspec/changes/source-grounded-education/specs/' "$trace_log" || true
! rg -n '^(ERROR|WARN).*openspec/changes/source-grounded-education/specs/' "$trace_log"
printf 'repository-wide trace exit: %s\n' "$trace_rc"
tail -n 1 "$trace_log"
python3 scripts/check_cited_requirements_resolve.py
```

Run the strict overwrite projection separately. Its repository-wide exit remains non-zero while
frozen debt exists, so retain every source-grounded Education finding with its capability and
requirement name, then assert that the rebuilt curriculum requirement has no finding. This check
must not be replaced with an ID-only filter: strict overwrite diagnostics do not carry IDs.

```bash
overwrite_log="$(mktemp /tmp/source-grounded-education-overwrite.XXXXXX.log)"
python3 scripts/check_spec_overwrites.py --strict >"$overwrite_log" 2>&1
overwrite_rc=$?
awk '
  /^source-grounded-education -> / { print_block = 1 }
  print_block { print }
  print_block && /^$/ { print_block = 0 }
' "$overwrite_log"
! rg -n '^source-grounded-education -> module-education-curriculum "Topic decomposition into concept graph"$' \
  "$overwrite_log"
printf 'repository-wide strict overwrite exit: %s\n' "$overwrite_rc"
tail -n 1 "$overwrite_log"
```

## Validation record

Recorded on 2026-08-27 from the corrected, coherent `bu-istke.8` branch after incorporating
`bu-istke.7`:

- `openspec validate source-grounded-education --strict` exited 0.
- The active-change path projection returned no `ERROR` or `WARN` lines in authoring or strict
  trace mode. This path-based projection retains name-only structural diagnostics that the retired
  ID-only projection hid.
- The unfiltered authoring trace run exited 1 with
  `2410 requirements, 113 IDs, 2845 errors, 58 warnings`; the unfiltered strict trace run exited 1
  with `2410 requirements, 113 IDs, 2903 errors, 0 warnings`. Those repository-wide baseline
  findings are outside this change.
- `python3 scripts/check_cited_requirements_resolve.py` exited 0 and listed all seven backend-cited
  requirement IDs as provisionally resolved by this active change. The trace command above also
  scans `frontend/src` and resolves `REQ-dashboard-education-ui-001` to
  `NodeDetailPanel.test.tsx`.
- `python3 scripts/check_spec_overwrites.py --strict` exited 1 with the repository's existing
  unfrozen/frozen-debt projection. Its complete source-grounded Education projection contains no
  finding for `module-education-curriculum :: Topic decomposition into concept graph`, proving the
  two numbered baseline clauses survive independently. It still reports the pre-existing
  `module-education-teaching-flows :: Teaching Phase — Explain, Question, Evaluate` prose finding;
  that finding was not hidden or reclassified by this correction.
- `make check-spec-overwrites` exited 0 with no unfrozen baseline losses; the ratchet reported
  four fewer frozen losses for `MANIFESTO.md Content`, seven fewer for `Topic decomposition into
  concept graph`, and five fewer for `Teaching Phase — Explain, Question, Evaluate`. The overwrite
  baseline was not replaced or updated.
- The capability-qualified unarchived collision scan found eight duplicate groups repo-wide and
  exactly the two Education collisions recorded above for this change.
- The focused Education backend evidence suite passed 50 tests, including the manifesto,
  curriculum, pedagogy, source-registry API, and reading-pathway cases cited above; the focused
  `NodeDetailPanel` frontend suite passed 12 tests.
- `git diff --check` exited 0.

This evidence is scoped. It does not authorize archiving the change, modifying any other
`MODIFIED` requirement body, or claiming the repository-wide baseline is clean.
