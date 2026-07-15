---
name: patrol-operations
description: QA patrol operations reference — the step sequences for the patrol loop, reactive finding reception, investigation management, and PR status tracking
version: 1.0.0
tools_required:
  - force_patrol
  - get_qa_status
---

# QA Patrol Operations Skill

## Purpose

Load this skill when executing or reasoning about a patrol cycle, a finding
reception, an investigation, or PR status tracking. It documents the concrete
step sequence for each of the QA Staffer's core operations.

## 1. Patrol Loop Execution

When your scheduled patrol tick fires:
1. Create a `qa_patrols` record in the DB
2. Poll all enabled discovery sources (log_scanner, session_records, butler_reports)
3. Triage findings: deduplicate against active investigations, dismissals, cooldown
4. Dispatch novel findings for investigation (up to `max_concurrent_investigations`)
5. Update the patrol record with outcomes

## 2. Reactive Finding Reception

When `report_finding` is called (via Switchboard routing from a butler):
1. Accept the finding immediately into the `butler_reports` buffer
2. Return `{"accepted": true}` synchronously
3. If severity == 0 (critical), trigger an immediate mini-patrol

## 3. Investigation Management

Each investigation:
1. Creates a worktree with `qa/` prefix
2. Spawns an isolated agent with sandboxed environment (no butler secrets)
3. Monitors outcome via watchdog timeout
4. Reports result (PR created, unfixable, failed, timeout)

## 4. PR Status Tracking

On each patrol cycle, check `pr_open` investigations:
1. Query GitHub for current PR status
2. Transition to `pr_merged` or `failed` as appropriate
