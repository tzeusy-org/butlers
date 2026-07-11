# `butlers deploy`: One Idempotent Production Deploy Verb

## Why

bd bu-zhfd0: seven merged core-chain revisions (core_155..161) sat dark in
prod for six days because the one-shot `migrations` compose service had
already exited 0 once against a *pre-core_155* image, and `docker compose up
-d` treats `service_completed_successfully` as permanently satisfied once a
container has exited 0 — rebuilding the image with new migrations baked in
does not force a rerun. Separately, `docker compose` reads `COMPOSE_PROFILES`
from the ambient shell environment even with zero `--profile` flags on the
command line — a shell left over from a dev session (`COMPOSE_PROFILES=
hotreload`) would silently recreate prod under the bind-mounted hotreload
services. The prod-deploy ceremony was otherwise entirely manual: a human
builds an image, runs `docker compose up -d`, and eyeballs that things came
back up — with no record of whether it worked.

This is slice 3 of epic bu-9r3hd ("Deploy spine"), sibling to
`deploy-drift-sentinel` (bu-9r3hd.1, the migration-drift sentinel) and
`bu-9r3hd.2` (the `public.deployments` ledger, already merged — PR #3082).
That change's proposal explicitly deferred this one: "`butlers deploy`
one-command build/migrate/verify-health pipeline (bu-9r3hd.3)" (see
`openspec/changes/deploy-drift-sentinel/proposal.md` §Out of Scope). This
change is that slice.

Doctrine anchor: `about/heart-and-soul/vision.md` §"What Success Looks Like"
— "Butlers succeeds when it runs for weeks without intervention." An
artisanal deploy ceremony with no idempotency guarantee and no failure record
is the opposite of that.

## What Changes

- **`butlers deploy`** (new CLI verb, `src/butlers/core/deploy.py` +
  `src/butlers/cli.py`): one idempotent command that (1) builds the
  `butlers-app` image stamped with the current git SHA, (2) force-reruns the
  one-shot `migrations` service via `docker compose run --rm` (never trusts a
  stale exited container's `service_completed_successfully`), (3) recreates
  services via `docker compose up -d --remove-orphans` with **zero**
  `--profile` flags and a subprocess environment stripped of any inherited
  `COMPOSE_PROFILES` (so the merged config is always exactly the default,
  profile-less, baked-image service set — the hotreload/dev-only services
  cannot be selected, by construction, regardless of ambient shell state),
  (4) polls `/health` until it reports `status: "ok"` or a bounded timeout
  elapses, and (5) records the outcome to `public.deployments`
  (`butlers.core.deployments.record_deployment`, from bu-9r3hd.2) — on
  **both** success and failure, so a failed deploy is visible in the ledger
  rather than silent.
- **Extends capability `deployment-and-drift`** (new requirement; the
  capability already houses the sibling drift-sentinel contract) rather than
  introducing a new capability — both are slices of the same "deploy spine"
  epic and share the same `public.deployments` / migration-chain vocabulary.
- No new database migration — this slice only calls the existing
  `record_deployment` writer (bu-9r3hd.2's `core_163` table).
- `docker-compose.yml`'s header gains a "PROD DEPLOYS" comment documenting
  the profile-isolation contract inline, so a human editing the compose file
  sees the constraint before accidentally tagging a prod-relevant service
  with a new profile.

## Out of Scope (deferred to sibling epic slices)

- Feeding infra-health signals (including a failed/stale deploy) into the QA
  patrol's `DiscoverySource` pipeline (bu-9r3hd.4).
- Backup status honesty (bu-9r3hd.5).
- Actually verifying container recreation end-to-end against a live prod
  host — this cannot run in CI (no live Docker daemon standing up the full
  stack with real Postgres/Tailscale reachability); the orchestration logic
  is unit-tested with every subprocess/HTTP boundary mocked, and the ledger
  write is integration-tested against a real migrated Postgres. Host
  verification is tracked as a follow-up.

## Impact

- Affected specs: `deployment-and-drift` (new requirement, this change).
- Affected code: `src/butlers/core/deploy.py` (new), `src/butlers/cli.py`
  (`deploy` command), `docker-compose.yml` (header comment only).
- No database migration in this slice.
