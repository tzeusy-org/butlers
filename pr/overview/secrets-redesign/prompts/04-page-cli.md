# 04 · The CLI runtime page

> Phase E. End state: clicking any CLI-runtime row renders its page
> with how-to-use snippet and rotate flow.

## Source of truth

`secrets-pages.jsx#PageCli`.

## What's different about CLI runtimes

A CLI runtime is a token held by a command-line agent (Claude Code,
Codex, Gemini, …) used to authenticate against the system. They:

- Are owner-only.
- Carry session-scoped capabilities, not OAuth scopes against a
  third party.
- Have no `feeds` (they don't power butler features; they enable
  CLIs to call the API).
- Have no `breaks` block (failure means a CLI can't talk; this is
  immediate and self-evident).
- Get a `how to use` snippet — a single mono command line showing
  how to set the token in the environment.

## Anatomy

```
┌────────────────────────────────────────────────────────────────┐
│ COMMAND-LINE AGENT · codex-cli                                 │
│                                                                │
│ Codex CLI                              [   EXPIRING    ]       │
│ codex-cli                              [ expires 2026-05-29 ]  │
│                                                                │
│ Token used by the Codex CLI to authenticate against the system.│
│                                                                │
│ ──────────────────────────────────────────────────────────────│
│ PASSPORT NO.   │ ISSUED   │ EXPIRES   │ LAST USED              │
│ sha256:9f0a…   │ 2025-11  │ 2026-05   │ 4d ago                 │
│ ──────────────────────────────────────────────────────────────│
│                                                                │
│ CAPABILITIES                       PROBE · last test           │
│ ✓ repo.write       granted         ● 200  110ms  at 4d ago     │
│                                                                │
│ HOW TO USE                                                     │
│ $ codex-cli --token $(CODEX_CLI_TOKEN)                         │
│ ──────────────────────────────────────────────────────────────│
│ ELSEWHERE              │ CONFIG                                │
│ /audit?actor=codex-cli │ runtime · codex-cli                   │
│                        │ scope · session-bound                 │
│ ──────────────────────────────────────────────────────────────│
│ [rotate · commit] [test]                       [reveal] [revoke]│
└────────────────────────────────────────────────────────────────┘
```

## Implementation notes

### `<CapabilitiesBlock>`

Same atom as `<VisaRow>` from the user page, relabelled. CLI runtimes
typically have one or two capabilities. If the runtime has no
capabilities required (`scopesRequired: []`), omit the block.

### `<HowToUse>` snippet

```
$ <id> --token $(<ID_TOKEN>)
```

Rendered in a `1px solid var(--border-soft)` + `--bg-elev` block,
mono 11px fg. The `<ID_TOKEN>` is the canonical env-var name (uppercase
+ underscores). If your CLI uses a different env-var, surface the
real one — server can return a `tokenEnvVar` field on the runtime
shape; default fallback is `runtime.id.toUpperCase().replace('-',
'_') + '_TOKEN'`.

### Rotate flow

`POST /api/secrets/cli/<id>/rotate` returns:

```json
{ "fingerprint": "sha256:…", "value": "tk_…", "issuedAt": "…" }
```

The new value is returned **once**. Show it in a modal with a
copy-to-clipboard affordance, big mono. After the user dismisses,
the value is gone from the client; the page state shows the new
fingerprint only.

Make the modal hard to dismiss accidentally — a checkbox `I have
saved this token` must be ticked before `Done` activates. The value
must not appear in any analytics event or DevTools network log for
later requests.

### State commits

- `never_set` → `[set token]` commit pill. Opens a paste-token modal.
- `expiring` → `[rotate]` commit + `[test]` regular.
- `ok` → `[rotate]` `[test]` (both regular).
- `expired / revoked` → `[set token]` commit (treat as never_set).

## Acceptance for this phase

- [ ] All states render. Codex CLI (expiring) shows the amber plaque.
- [ ] `[rotate]` returns the value once, in a modal with copy-clip
      and an `I have saved this token` checkbox.
- [ ] `[revoke]` requires confirmation and immediately disables the
      runtime.
- [ ] The how-to-use snippet matches the CLI's actual env-var.
