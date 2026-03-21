# Education Butler

> **Purpose:** Expert adaptive tutor with spaced repetition, mind maps, and personalized learning that transforms curiosity into lasting mastery.
> **Audience:** Contributors and operators.
> **Prerequisites:** [Concepts](../concepts/butler-lifecycle.md), [Architecture](../architecture/butler-daemon.md).

## Overview

The Education Butler is a patient, knowledgeable tutor that teaches any topic through conversation. It calibrates to the user's existing knowledge through diagnostic assessments, builds personalized curricula as mind map DAGs, teaches one concept at a time using Socratic questioning, and schedules spaced repetition reviews at algorithmically optimal intervals to convert short-term comprehension into permanent knowledge.

The butler uses a top-tier model (gpt-5.2 with high reasoning effort) specifically because teaching requires nuanced judgment: calibrating difficulty, generating precise quiz questions, evaluating free-form answers, and knowing when to push deeper versus step back.

The Education Butler does not deliver video content, connect to external learning platforms, issue certifications, or manage group learning. All teaching happens through conversation, one person at a time, at their own pace.

## Profile

| Property | Value |
|----------|-------|
| **Port** | 41107 |
| **Schema** | `education` |
| **Modules** | education, memory, contacts |
| **Runtime** | codex (gpt-5.2, reasoning_effort=high) |

## Schedule

| Task | Cron | Description |
|------|------|-------------|
| `nightly-analytics` | `0 3 * * *` | Compute analytics snapshots for all active mind maps |
| `weekly-progress-digest` | `0 9 * * 0` | Weekly learning progress digest: velocity trends, retention rates, struggling areas, achievements. Delivered via Telegram. |
| `weekly-stale-flow-check` | `0 4 * * 1` | Clean up stale teaching flows (inactive for 30+ days) and remove pending review schedules |
| `daily-spaced-repetition-nudge` | `0 17 * * *` | Daily check for pending spaced repetition reviews. Sends one summary message if reviews are due; silent if nothing is pending. |

## Tools

**Mind Map Tools**
- `mind_map_create / get / list / update_status` -- Manage mind maps (active, completed, abandoned) that represent knowledge domains.
- `mind_map_node_create / get / update / list` -- Add and manage concept nodes with mastery scores and status (unseen, learning, reviewing, mastered).
- `mind_map_edge_create / delete` -- Define prerequisite edges between concepts with DAG acyclicity enforcement.
- `mind_map_frontier` -- Get frontier nodes: prerequisites mastered but node itself not yet mastered. These are the next teachable concepts.
- `mind_map_subtree` -- Get all descendants of a node via recursive CTE.

**Teaching Flow Tools**
- `teaching_flow_start / get / advance / abandon / list` -- Manage the teaching state machine. Each flow progresses through phases: DIAGNOSING, PLANNING, TEACHING, QUIZZING, REVIEWING, COMPLETED.

**Mastery Tools**
- `mastery_record_response` -- Record a quiz response, update mastery score and status, run the SM-2 spaced repetition algorithm.
- `mastery_get_node_history / get_map_summary` -- Quiz history and aggregate mastery stats.
- `mastery_detect_struggles` -- Identify nodes with declining or persistently low mastery.

**Spaced Repetition Tools**
- `spaced_repetition_record_response` -- Record review result, compute next interval, schedule next review.
- `spaced_repetition_pending_reviews` -- Get nodes due for review (next_review_at <= now).
- `spaced_repetition_schedule_cleanup` -- Remove pending schedules for completed or abandoned maps.

**Diagnostic Assessment Tools**
- `diagnostic_start` -- Initialize diagnostic session and generate concept inventory.
- `diagnostic_record_probe / complete` -- Record probe question results and finalize diagnostic.

**Curriculum Planning Tools**
- `curriculum_generate` -- Decompose a topic into a concept DAG, run topological sort, and assign learning sequence.
- `curriculum_replan / next_node` -- Re-compute learning sequence based on current mastery or get the next concept to teach.

**Analytics Tools**
- `analytics_get_snapshot / trend / cross_topic` -- Learning analytics: per-map snapshots, time-series trends, and cross-topic comparison.

## Key Behaviors

**Diagnostic Calibration.** Before the first lesson on any topic, the butler runs a diagnostic: a short sequence of probe questions that map the user's existing knowledge. Teaching starts where the user actually is, skipping concepts already understood and not skipping missing foundations.

**One Concept Per Session.** Each session handles exactly one teaching phase. The butler never chains phases within a single session. State is persisted via `teaching_flow_advance()` before exiting, because the next session has no memory of the current one.

**Socratic Questioning.** Before explaining a concept, the butler asks what the user already knows. Understanding revealed through dialogue sticks better than passively received explanation. The butler never says "wrong" -- it uses Socratic nudges and guiding questions for incorrect answers.

**Spaced Repetition.** After learning a concept, the SM-2 algorithm schedules reviews at optimal intervals. The daily nudge at 17:00 checks for pending reviews and sends one actionable summary if any are due.

**Entity-Backed Concepts.** Every mind map node is backed by a `shared.entities` entity, enabling memory deduplication. Facts about learning outcomes and struggles are stored with `entity_id` links to the canonical concept entity.

## Interaction Patterns

**Starting a topic.** The user says "Teach me Python" and the butler begins with a diagnostic calibration, then builds a personalized curriculum and begins teaching one concept at a time.

**Review sessions.** When spaced repetition reviews come due, the daily nudge notifies the user. They reply "review" and the butler runs through pending quiz questions, scoring responses and scheduling the next review.

**Progress queries.** Users ask "How am I doing on Python?" and receive a data-backed answer with mastery percentages, retention rates, and estimated completion dates.

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes learning-related messages here
- [General Butler](general.md) -- handles general knowledge storage that is not part of a structured curriculum
