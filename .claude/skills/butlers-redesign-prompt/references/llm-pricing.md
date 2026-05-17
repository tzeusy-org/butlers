# LLM Pricing — Phase D cost-audit reference

> **last_verified: 2026-01** (assistant knowledge cutoff).
>
> Phase D subagents: if today's date is more than 60 days past `last_verified`, fetch live pricing from `https://www.anthropic.com/pricing` before computing dollar verdicts. Use the table below as the source for tokens-to-dollars math.

## Claude pricing — per 1 million tokens (USD)

| Model class | Input | Output | Cache write (5 min TTL) | Cache write (1 h TTL) | Cache hit |
|-------------|-------|--------|-------------------------|-----------------------|-----------|
| Opus 4.x    | $15   | $75    | $18.75                  | $30                   | $1.50     |
| Sonnet 4.x  | $3    | $15    | $3.75                   | $6                    | $0.30     |
| Haiku 4.x   | $1    | $5     | $1.25                   | $2                    | $0.10     |

Cache write multipliers: 1.25× base input for 5-min TTL, 2× base input for 1-h TTL. Cache hits: 0.1× base input. These multipliers are stable across model classes; the per-MTok table already applies them.

## How to compute cost in Phase D

For every LLM-driven feature in the redesign, estimate four numbers and derive the rest:

```
tokens_in        # per call (include system prompt + MCP tool docs + conversation context)
tokens_out       # per call (response length the feature actually needs)
model_class      # Opus | Sonnet | Haiku
frequency        # calls per active user per day

input_rate       # from table above, per 1M tokens
output_rate      # from table above, per 1M tokens

cost_per_call     = (tokens_in * input_rate + tokens_out * output_rate) / 1_000_000
cost_per_user_day = cost_per_call * frequency
cost_per_user_mon = cost_per_user_day * 30
```

Always show the arithmetic in the Phase D table. "$0.21/user/day = 2,400 tokens × $3/MTok input + 150 tokens × $15/MTok output × 24 calls" beats "looks expensive".

## Frequency estimation — common cadences

| Trigger pattern | calls/user/day |
|-----------------|----------------|
| Live recompose every 4 s (status pill style) | 21,600 |
| Live recompose every 30 s | 2,880 |
| Live recompose every minute | 1,440 |
| Refresh every 5 min while page open (assume 2 h open) | 24 |
| Refresh every hour | 24 |
| Page-load only (assume 10 page loads/day) | 10 |
| On-demand action (button click) | 1–5 |
| Background daily summary | 1 |

Live cadences are the budget killers. Anything below 5-minute cadence on Sonnet is `yellow` even with small payloads; anything below 1-minute cadence is almost certainly `red`.

## Sanity-default per-affordance rows

When the bundle does not let you measure precisely, use these defensible defaults and cite which row you used.

| Affordance shape                                  | tokens_in | tokens_out | model  | typical freq/day |
|---------------------------------------------------|-----------|------------|--------|------------------|
| Single-line live status pill                      | 1,500     | 30         | Haiku  | 21,600 (4 s live) or 24 (hourly) |
| One-paragraph summary card                        | 3,000     | 200        | Sonnet | 5–20             |
| Inline classification badge (low/med/high)        | 800       | 5          | Haiku  | 50–200           |
| Smart suggestion / next-action chip               | 2,500     | 60         | Sonnet | 5–50             |
| Multi-step agent (3+ tool calls)                  | 8,000     | 600        | Sonnet | 1–5              |
| Free-form draft on demand                         | 5,000     | 800        | Sonnet | 1–10             |
| Search-result reranker                            | 4,000     | 150        | Haiku  | 10–100           |
| Narrative caption regenerated on time-range change| 2,000     | 250        | Sonnet | 5–50             |

## Verdict thresholds

Per active user per day (`cost_per_user_day`):

- **green** — < $0.05/day. Ship as designed.
- **yellow** — $0.05–$0.50/day. Ship with a cache/throttle/debounce strategy spelled out in the verdict notes.
- **red** — > $0.50/day **OR** sub-minute trigger cadence **OR** no obvious cache strategy **OR** contradicts a `## 0. Design intent` "what we are deliberately NOT doing" bullet.

Red verdicts must be one of: de-scoped, reshaped (cache/throttle/debounce/lazy-load), or escalated to the user before reaching `/project-direction`.

## Caching strategy hints (use in `yellow` write-ups)

- **5-min cache** is the cheapest sustained-context strategy; use for anything where staleness < 5 min is acceptable.
- **1-h cache** for slowly-changing context (manifesto, butler config, account inventory).
- **On-demand only** is the safest pattern — replace "live recompose" with "click to refresh" and the cost collapses by 100×.
- **Background pre-compute** (run hourly, store result) turns live affordances into reads-against-cache; the LLM call moves to a scheduled job whose cost is `1 call × frequency_job`, not `1 call × users × pageviews`.

## Per-account scaling notes (Butlers-specific)

Butlers v1 targets single-user (one owner per deployment), so `users = 1` for v1 cost math. Run the math at `users = 100` as a "what if we open this up" sensitivity column. If `users = 100` math collapses the budget, flag that explicitly in the verdict notes even if v1 math is green.
