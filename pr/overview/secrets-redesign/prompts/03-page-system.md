# 03 · The System secret page

> Phase D. End state: clicking any system-secret row renders an
> editorial page for that `butler_secrets` entry. Three row-states
> render distinctly: `shared`, `local`, `missing`.

## Source of truth

`secrets-pages.jsx#PageSystem`.

## What changes vs. the User page

System secrets are simpler than user secrets:

- **No scopes.** A system secret is either filled or not.
- **No identity.** They live system-side.
- **No OAuth dance.** The connect/rotate flow is always a paste-value
  modal.
- **`rowState`** replaces `state`. Three values: `shared`, `local`,
  `missing`.
- The state plaque carries one of: `shared default` / `local
  override` / `not set`. No red. Local override colour is calm fg;
  missing is dim.

## Anatomy

```
┌────────────────────────────────────────────────────────────────┐
│ CATEGORY · telegram                                            │
│                                                                │
│ BUTLER_TELEGRAM_TOKEN                  [  SHARED DEFAULT  ]    │
│                                        [  verified 14:20  ]    │
│                                                                │
│ Bot API token for system-wide Telegram I/O.                    │
│                                                                │
│ ──────────────────────────────────────────────────────────────│
│ FINGERPRINT  │ LAST VERIFIED   │ USED BY                       │
│ sha256:5e9c… │ 14:20 today     │ [s] switchboard [r] relat…    │
│ ──────────────────────────────────────────────────────────────│
│                                                                │
│ WHAT BREAKS                       PROBE · last test            │
│ ▰ inbound telegram   breaks ·     ● 200  41ms  at 14:20        │
│   switchboard                                                  │
│ ▰ outbound replies   breaks ·     STAMPS · audit               │
│   switchboard                     [✓] 2026-05-23 14:20 · ver…  │
│                                   [↻] 2025-09-14 11:05 · rot…  │
│                                                                │
│ ──────────────────────────────────────────────────────────────│
│ ELSEWHERE              │ STORAGE                               │
│ /audit?key=BUTLER_…    │ butler_secrets · shared               │
│ /butlers/switchboard   │ category · telegram                   │
│                        │ scope · shared default                │
│ ──────────────────────────────────────────────────────────────│
│ [test] [rotate] [override · per butler]   [reveal] [delete]    │
└────────────────────────────────────────────────────────────────┘
```

Key differences from the user page:

### Heading title is mono

The system-secret key (`BUTLER_TELEGRAM_TOKEN`) is rendered in
JetBrains Mono, 24px weight 500. No letter-mark; system secrets don't
have an "issuing authority".

### The KV band is shorter

```
200px (fingerprint or value) · 140px (last verified) · 1fr (used by)
```

When `plainValue` is set (e.g. `GMAIL_SENDER_ADDRESS = tze@lim.house`)
swap the eyebrow from `FINGERPRINT` to `VALUE` and show the value in
fg mono — no fingerprint redaction, since the value is not a secret.

### `used by` cell

- Empty `[]` → `nobody yet` (mono dim).
- `['*']` → render the canonical sentence in **serif italic**:
  *"every butler that talks to a model."* (Reserved for the
  `ANTHROPIC_API_KEY`-style ubiquitous secrets.)
- Otherwise: a horizontal flex of `<ButlerMark>` + name pairs.

### `local` override

When `rowState === 'local'`, the state plaque reads `LOCAL OVERRIDE`
with a `target · <butler>` line. The commits footer shows
`[remove override]` instead of `[override · per butler]`.

### `missing`

Plaque reads `NOT SET` (dim). No KV band fingerprint cell; only the
description and a single commit: `[set value]` (commit pill). The
*what breaks* block, if populated, renders in the "what would break"
mode (future tense, no colour).

### Cross-references

- `elsewhere` column lists `/audit?key=<key>` and, if there's a
  specific dependent butler, `/butlers/<butler>`.
- `storage` column lists `butler_secrets · <target>`, `category ·
  <category>`, `scope · shared default | per butler`.

## Mutations

| Action | Endpoint | Notes |
|---|---|---|
| Set value (when missing) | `POST /api/secrets/system/<key>` body `{ value, target: 'shared' }` | |
| Override per-butler | `POST .../system/<key>` body `{ value, target: '<butler>' }` | Creates a `local` row alongside the shared one. |
| Remove override | `DELETE .../system/<key>?target=<butler>` | Falls back to shared default. |
| Rotate | `POST .../system/<key>` body `{ value, target: '<currentTarget>' }` | Same shape as set. |
| Probe | `POST .../system/<key>/probe` | |
| Reveal | `POST .../system/<key>/reveal` | Returns value once. |
| Delete (shared) | `DELETE .../system/<key>?target=shared` | Hard delete — confirmation required. |

## Acceptance for this phase

- [ ] All three rowStates render distinctly.
- [ ] `plainValue` branch (Gmail sender) shows the value in clear,
      and the `VALUE` eyebrow.
- [ ] `['*']` `usedBy` shows the serif italic fallback sentence.
- [ ] `[override · per butler]` opens a modal listing every butler;
      saving creates a new `local` row.
- [ ] Probe works for every probe-able key (Telegram, Anthropic,
      OpenAI, S3, OAuth client secrets).
- [ ] Delete confirmation modal blocks the destructive action.
