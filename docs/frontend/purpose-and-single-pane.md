# Frontend Purpose and Single-Pane Role

## Product Purpose

The frontend is the operations console for the Butlers system: a web UI for understanding system health, inspecting runtime behavior, and performing targeted admin actions.

It is not the primary conversational interface. Chat and channel surfaces remain primary for end-user interaction; the dashboard is the control/observability plane.

## Why It Must Be a Single Pane of Glass

The backend is distributed across multiple butlers, modules, and databases. Without a unified UI, operators must jump between logs, DB queries, and daemon endpoints.

The frontend is intentionally the single pane that combines:

- Cross-butler status and topology.
- Connector health, uptime, and ingestion volume visibility.
- Session and trace visibility.
- Notifications and auditability.
- Approval queue and standing-rule governance for high-impact autonomous actions (target integration).
- Domain data browsing (relationship, health, general entities, memory).
- Safe-but-powerful admin controls (trigger, schedules, state).

This single-pane role reduces operational latency for three critical loops:

- Detect: identify what is failing, degraded, or expensive.
- Diagnose: inspect sessions, traces, state, and timeline context.
- Act: trigger runs, update schedules, and correct state from one UI.

## Scope Boundaries (Current)

### In Scope

- Monitoring and diagnostics across butlers.
- Read-heavy data exploration across domain surfaces.
- Selected write/admin operations through dashboard API endpoints.
- Keyboard-first navigation and quick search for operational speed.

### Out of Scope

- Replacing chat as the main user interaction path.
- Full CRUD for every domain entity (many domain screens are read-focused).
- End-user workflow UX (this is an operator/admin dashboard).

## Current Maturity Summary

- Mature read visibility across most domains.
- Operational writes exist for butler trigger, schedules, and state store.
- Approval workflows are currently MCP-tool driven and not yet represented as a dedicated frontend surface.
- Some planned surfaces remain partial or placeholder (documented in `docs/frontend/feature-inventory.md`).
