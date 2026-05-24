# Secrets redesign — index

Quick navigation for the hand-off pack. Skim this before diving into
`HANDOFF.md`.

## Read in this order

| # | File | What you get |
|---|------|--------------|
| 1 | [`README.md`](README.md) | Thesis, folder layout, the five rules |
| 2 | [`BRIEF.md`](BRIEF.md) | Stage 1 brief — what the page does, the hardest call, decisions taken |
| 3 | [`HANDOFF.md`](HANDOFF.md) | The implementation prompt for Claude Code |
| 4 | [`DESIGN_LANGUAGE.md`](DESIGN_LANGUAGE.md) | The full Dispatch spec — canonical |
| 5 | [`prompts/`](prompts/) | Per-surface implementation prompts |
| 6 | [`Secrets.html`](Secrets.html) | The interactive prototype (open in a browser) |

## Per-surface prompts

| # | File | Maps to |
|---|------|---------|
| 00 | [`prompts/00-foundation.md`](prompts/00-foundation.md) | data model, API surface, routes, IA |
| 01 | [`prompts/01-spine.md`](prompts/01-spine.md) | the left index (search, sort, identity, pinned needs-hand) |
| 02 | [`prompts/02-page-user.md`](prompts/02-page-user.md) | integration page (oauth / token / apikey / webhook) |
| 03 | [`prompts/03-page-system.md`](prompts/03-page-system.md) | system secret page |
| 04 | [`prompts/04-page-cli.md`](prompts/04-page-cli.md) | CLI runtime page |
| 05 | [`prompts/05-tweaks-and-state.md`](prompts/05-tweaks-and-state.md) | tweaks panel, URL state, persistence |

## Prototype source map

| Layer | File | Purpose |
|---|---|---|
| Palette | `primitives.jsx` | `applyTheme`, `C` (live tokens), `ButlerMark`, `Spark` |
| Data | `secrets-data.jsx` | provider catalog, user/system/CLI sample records, state catalog |
| Base atoms | `secrets-shared.jsx` | `Mono`, `Voice`, `Display`, `ProviderMark`, `StateDot`, `Sliver`, `Fingerprint`, `ScopeRow`, `PillBtn` |
| Evidence atoms | `secrets-evidence.jsx` | `WhatBreaks`, `ProbeResult`, `ScopeBalance`, `IdentityChip`, `StampGlyph`, `FingerprintRow` |
| Spine | `secrets-spine.jsx` | `Spine`, `buildSpineEntries`, sort + search + identity switcher |
| Pages | `secrets-pages.jsx` | `PageUser`, `PageSystem`, `PageCli` |
| Composition | `secrets-passport.jsx` | `DirectionPassport` — orchestrates everything |
| Tweaks | `secrets-tweaks.jsx` + `tweaks-panel.jsx` | reveal mode, default sort |
| Entry | `Secrets.html` | loads all scripts, mounts `<SecretsApp />` |

## Quick checks before you ship

- [ ] The prototype renders in any browser (open `Secrets.html`).
- [ ] No console errors.
- [ ] Clicking any spine row flips the page in the right half.
- [ ] The identity switcher (Tze / Wei) re-projects user secrets.
- [ ] Sort radios change the in-section order.
- [ ] Search filters the spine in real time.
- [ ] The Spotify page (expired) shows a red plaque + failure tail
      + the "what breaks" block.
- [ ] The WhatsApp page (scope mismatch) shows the missing scope
      called out in the visa list and in "what breaks".
- [ ] The Home Assistant page (expiring) shows the amber plaque +
      "expires 2026-05-27" line.
- [ ] The OwnTracks page (webhook) shows the incoming URL KV instead
      of the issued/expires columns.
- [ ] The Codex CLI page (expiring) shows the rotate-as-commit button.
- [ ] The Anthropic system page shows "every butler that talks to a
      model" as the serif fallback in `used by`.
- [ ] Tweaks toolbar toggle works; reveal-mode + default-sort persist
      via the EDITMODE block.
