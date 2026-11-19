# Switchboard Butler

The single ingress and routing control plane. It assigns request context, resolves sender
identity, classifies and decomposes each message, dispatches segments to specialist butlers, and
relays every butler's `notify()` to Messenger. It owns no domain logic.

- **Identity and scope:** [`roster/switchboard/MANIFESTO.md`](../../roster/switchboard/MANIFESTO.md)
- **Required behavior:** [`butler-switchboard` spec](../../openspec/specs/butler-switchboard/spec.md)
- **Schedules, modules, and port:** [`roster/switchboard/butler.toml`](../../roster/switchboard/butler.toml)

![Switchboard Design](./switchboard-design.svg)

## Insight outcome efficacy (observe-only)

The daily `insight_outcome_sweep` job (05:52 UTC) enrolls terminal premise-bound insights into
`public.insight_outcomes`, settles whether each premise resolved, then rolls the settled rows of
the last 90 days up into `public.insight_class_efficacy`: one row per origin butler, category,
priority band (`low` under 50, `normal`, `urgent` at 90 and above) and day. Treated is
`delivered`/`coalesced`; the holdout is `held_budget`/`held_context`; `unmeasurable` rows count in
neither arm. Under five measured rows in either arm the verdict is `unmeasured`. Otherwise it is
`effective` when the treated arm resolves at least 20 points more often, or about as often at no
more than half the holdout's median time; `self_resolving` when the holdout resolves about as
often and at least 60% of the time; else `ignored`. `GET /api/attention/efficacy` returns the
latest row per class and band, and the Trust Console's attention-ledger panel lists them. Nothing
reads the verdicts to shape delivery yet.

Read a verdict as a correlation, not an effect:

- **Holdout selection is biased.** Nothing is withheld at random. A candidate is held because the
  day's budget ran out or the owner's context said wait, so the held arm skews toward
  lower-ranked candidates and busy days, and it can differ from the treated arm in ways that
  also change whether the premise resolves.
- **The treated window is shorter after a delay.** Both arms are timed and windowed from
  `proposed_at`, because a holdout has no exposure time. A candidate delivered late (quiet hours,
  a coalesced digest) has less of its 14-day window left after the owner sees it, and its time
  to resolve includes time before the owner was told. Both effects understate a nudge's effect.

## Related Pages

- [Switchboard Routing](../concepts/switchboard-routing.md) -- how a message is routed, end to end
- [Messenger Butler](messenger.md) -- the delivery plane `notify()` is relayed to
- [General Butler](general.md) -- fallback routing target
