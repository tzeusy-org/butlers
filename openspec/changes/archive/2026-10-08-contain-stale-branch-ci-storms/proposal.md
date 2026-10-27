# Contain stale-branch CI storms

## Why

Old branch workflow YAML can trigger non-main push storms even though current main limits push. Live PR edits also need validated metadata rather than a frozen event copy.

## What Changes

Historical 2026-10-03 push storms from old branches carrying unfiltered CI YAML delayed genuine PR feedback for hours. Released run17 sources are ci-runner-infra#1, measure-ci-critical-path#7 and flake-and-queue#6 from docs/redesigns/2026-10-06-sdlc-velocity-pursuit.md and its full structured data. Historical 1,515/896/at-least-1,000 counts use different source windows; 2,075 branches, 99% cache pressure, 5h50m/4.6h waits and the 39-minute body-fix payoff are dated evidence, not current gains. bu-7lh5ew is CLOSED; bu-gvut0 and the proposed seven-minute doctrine target remain separate and unadopted.

## Steps to Reproduce
At pinned fb7c main CI filters pushes to main, while actual historical push runs 37151268349 and 37151267998 resolve heads whose .github/workflows/ci.yml has unfiltered push. A bulk re-push of those refs evaluates their old YAML; editing main alone cannot remove this lineage. Current CI guards writes title/body from the frozen pull_request event, so a body-only correction is invisible to a rerun. QA dispatch fetch failure falls back to local main despite its latest-main contract. These are source-backed diagnoses, not newly executed falsification runs in this PRIMARY phase.

Read-only snapshots at 2026-10-08 04:07 UTC found 2,084 remote heads, delete_branch_on_merge=false, 93 caches/10,700,877,063 bytes, 90 node-cache entries with one genuine main node entry, 21 open PRs, and 1,905 closed-PR name matches. Most current branch heads differ from those PR heads: no row is authorized for deletion. The capped last-1,000 push-run sample is incomplete and cannot prove an elapsed window. Retain actual current snapshots, every original literal and all complete source objects; ci-runner-infra#1.integration_points is itself clipped in committed JSON and missing bytes are not invented.

Deliver all original S1–S3 plus the complete folded live-body repair: S1 safe setting/cache preparation and later authorized application/readback; S2 exact-head recoverable owner-approved branch cleanup under the under-100 target; S3 fresh successful origin/main QA preparation without unrelated-work loss and without routine active-PR rebase; folded live session-link guard repair with its forbidden-body positive and repaired-body companion. No slice is selected for deferral. Every original source, cache/main survivor, seven AND fourteen-day no-non-main-push outcome, docs/spec update and terminal-gate promise remains mandatory. The selected permitted timing route claims no wall-clock gain, with actual before/after state retained.

Default dry-run source, positioned tests, durable independent recovery and a concrete list precede root's final live-action approval request. Closed/merged PR names, stale age, a missing worker or main YAML are never deletion permission. Root alone owns canonical state, eventual explicit list approval/live application, observation and closure. Current PRIMARY changes only ignored artifacts; implementation/live/hosted/protected/elapsed outcomes are NOT RUN/UNMET.

## Impact

Trusted operator tooling, native QA initial preparation and shared Git custody, live PR metadata guard, testing contract and operational guidance. Required workflow contexts, permissions, test populations and foreign QA publication boundaries remain enforced. No live deletion, setting/cache mutation or public forbidden-link canary occurs in source preparation.
