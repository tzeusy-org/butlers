---
name: butlers-tooling
description: >
  Run, debug, QA, test, and observe the Butlers dev stack. Routes to one workflow: debug a Docker
  Compose dev-stack session / routing / connector / runtime failure (given a session, request, or
  trace id); invoke an end-to-end QA validation canary against the dashboard API; work a QA
  investigation PR to green (review threads, no PII/secrets, required checks); discover and prune the
  test suite (condensation); or generate/refresh Grafana dashboards from live Prometheus/Tempo.
  Triggers: "debug this session", "why did routing fail", "session id ...", "run the QA canary",
  "work this QA PR", "answer the review threads", "prune the test suite", "condense tests",
  "make/refresh a Grafana dashboard", "observability". Not for building butlers, connectors,
  schemas, specs, or UX — use /butlers-development.
metadata:
  owner: tze
  authors:
    - tze
    - Claude
  status: active
  last_reviewed: "2026-09-05"
---

# Butlers Tooling Router

Operate-and-maintain workflows for the Butlers dev stack. Each workflow is a full skill package under
`subskills/`. Load **at most one** per task. These execute against this repo's Docker Compose dev
environment and shared infrastructure; check each subskill's `compatibility` before running.

## Discover subskills

```bash
PKG="$(dirname "<absolute-path-to-this-SKILL.md>")"
find "$PKG/subskills" -maxdepth 2 -name SKILL.md
rg -n "^name:|^description:" "$PKG"/subskills/*/SKILL.md
```

## Routing table

| The task is... | Subskill | Typical trigger |
|---|---|---|
| Debug a dev-stack session failure, routing problem, or connector/runtime error — including from a session / request / trace id | [subskills/butler-dev-debug/SKILL.md](subskills/butler-dev-debug/SKILL.md) | "debug this session", "why did routing fail", "session id ..." |
| Run an end-to-end QA validation canary against the dashboard API and confirm the investigation ends unfixable | [subskills/butler-qa-invoke/SKILL.md](subskills/butler-qa-invoke/SKILL.md) | "run the QA canary", "invoke a QA validation" |
| Work a GitHub PR for a QA investigation to done: answer review threads inline, scrub PII/secrets, drive required checks green | [subskills/butler-qa-pr-review/SKILL.md](subskills/butler-qa-pr-review/SKILL.md) | "work this QA PR", "answer the review threads" |
| Discover, analyze, and prune the test suite toward contract-driven tests (condensation) | [subskills/butler-test-condensation/SKILL.md](subskills/butler-test-condensation/SKILL.md) | "prune the test suite", "condense tests", "assess test bloat" |
| Generate or refresh Grafana dashboard JSONs from live Prometheus/Tempo data | [subskills/generate-grafana-dashboards/SKILL.md](subskills/generate-grafana-dashboards/SKILL.md) | "make a Grafana dashboard", "refresh dashboards", "observability" |

## Routing rules

- One subskill per task. `butler-qa-invoke` and `butler-dev-debug` pair naturally (invoke a canary,
  then investigate it) but load sequentially, not together.
- **Operate vs build**: building butlers, connectors, schemas, specs, tool surfaces, or UX →
  `/butlers-development`. This router is for running, debugging, QA, testing, and observability.
- **Project knowledge vs execution**: doctrine, specs, topology, non-negotiables → `/doctrine`.
  Change-level engineering judgment (test rigor, diagnosis root-cause, flaky failures) →
  `/th-engineering`. Task tracking → `bd` (see `AGENTS.md`).
- No subskill fits → answer from this router or say so. Do not load a subskill to browse.
