# Preserve Fleet case history in offline recovery

## Why

The ordinary dump excludes forced case/link parents while retaining unfenced evidence. That can publish incomplete history and generic restore can ignore its failed foreign-key creation. Owner choice C authorizes a fixed offline import without changing runtime authority.

## What Changes

- Preserve cases, evidence and links in the existing exported snapshot and replay parents before children in one fail-closed transaction.
- Add owner-only INSERT policies and a fixed two-selector importer; runtime roles receive no execute authority.
- Audit complete population, expected validated/enabled foreign keys and absence of orphans before restore certification, including old ordinary evidence artifacts.
- Synchronize the Fleet Purpose, REQ-fleet-case-file-006, RFC0032 and backup completeness documentation.

## Impact

Core revision 288 follows current public core282. Backup, restore and their existing regression carriers change. Capture287 and unpublished authority/health revisions are separate contributions requiring actual later integration. No live restore, provider, schedule, private-schema access or evidence-mutation adoption occurs here.
