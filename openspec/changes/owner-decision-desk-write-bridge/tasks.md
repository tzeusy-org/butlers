## 1. Spec and facts

- [x] 1.1 Record the 2026-10-08 owner ruling as an amendment of `REQ-beads-projection-001`
      (`REQ-beads-projection-007`) and the `owner-decision-desk` capability.
- [x] 1.2 Verify the tracker facts in dev (reachability, auth, `bd` version and env names,
      scratch workspace, egress isolation) and record them in `design.md` and
      `docs/operations/kubernetes-deployment.md`.
- [x] 1.3 Create the least-privilege tracker user and its BWS dev keys without printing the value.

## 2. Bridge image and chart

- [x] 2.1 `Dockerfile.beads` with `bd` pinned by version and SHA-256; build and push it from
      `scripts/k8s/build-push.sh`.
- [x] 2.2 The CronJob runs the apply-then-export cycle with the dedicated Secret, the runtime
      Postgres env, and a scratch workspace.
- [x] 2.3 A dedicated ExternalSecret for the tracker credential and the tracker egress
      NetworkPolicy; the site-env pattern supplies the host and CIDR.
- [x] 2.4 Enable the bridge in `values.dev.yaml` (routing stays disabled).

## 3. Runtime

- [x] 3.1 Migration for `switchboard.decision_prompts` and `switchboard.decision_intents`.
- [x] 3.2 Intent store and recording contract, with tests.
- [x] 3.3 Owner intent route, connector prompt routes, credential allowlist, and the digest
      `intent` field, with tests.
- [x] 3.4 Applier script with reconciliation and the at-most-once contract, with tests.
- [x] 3.5 `decision_request` notify intent, Messenger rendering, and the `dsk1` signer, with tests.
- [x] 3.6 Connector `dsk1` callback handling, with tests.
- [x] 3.7 `decision_routing` job with quiet hours, budget, prompt reservation and ledger rows,
      with tests.
- [x] 3.8 Decisions detail renders the recorded intent state, with vitest.

## 4. Verification and rollout

- [x] 4.1 `openspec validate owner-decision-desk-write-bridge --strict`, `make check-guards`,
      `make lint`, and focused suites.
- [ ] 4.2 Deploy to `butlers-dev` after coordination; smoke the export, `/api/decisions`, and an
      API-recorded intent applied to a test decision bead.
- [ ] 4.3 Enable routing only after owner consent to live Telegram prompts.
