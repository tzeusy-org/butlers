# 05 · Tweaks & state

> Phase F. End state: the Tweaks toolbar toggle reveals a panel with
> per-page knobs that persist across reloads via the `EDITMODE`
> block.

## What the Tweaks panel controls

Four knobs, no more. The bar for adding a tweak is *"would the user
plausibly want this different on a per-install basis?"*.

| Knob | Type | Default | Effect |
|---|---|---|---|
| `revealMode` | radio | `eye` | How a credential value is exposed when the user wants to see it. `eye` shows a toggle button; `hover` reveals on hover-and-hold; `never` removes the affordance entirely. |
| `defaultSort` | radio | `severity` | Initial sort mode for the spine. URL param overrides. |
| `showVerifyCmd` | toggle | `true` | Whether the `+ verify cmd` expander under each fingerprint appears. |
| `voiceParagraph` | toggle | `true` | Whether the serif sentence under each page heading appears. When off, the state plaque + What-Breaks block carry the dramatic weight alone. |

## Persistence

The panel state lives in a single JSON block in `SecretsPage.tsx`:

```tsx
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "revealMode": "eye",
  "defaultSort": "severity",
  "showVerifyCmd": true,
  "voiceParagraph": true
}/*EDITMODE-END*/;
```

The host rewrites this block on disk when the user changes a tweak.
Do not move the block; do not split it; do not nest it. One block in
the page file, valid JSON between the markers.

## Protocol — wire it correctly or it silently fails

Order matters: **register the listener before announcing
availability.**

```tsx
useEffect(() => {
  const onMsg = (e) => {
    const t = e?.data?.type;
    if (t === '__activate_edit_mode') setOpen(true);
    if (t === '__deactivate_edit_mode') setOpen(false);
  };
  window.addEventListener('message', onMsg);
  // Then announce:
  window.parent.postMessage({ type: '__edit_mode_available' }, '*');
  return () => window.removeEventListener('message', onMsg);
}, []);
```

When the user changes a value, post the edit:

```ts
window.parent.postMessage(
  { type: '__edit_mode_set_keys', edits: { revealMode: 'hover' } },
  '*',
);
```

You can send partial updates — only the keys in `edits` are merged.

When the panel's own close button is clicked, dismiss:

```ts
window.parent.postMessage({ type: '__edit_mode_dismissed' }, '*');
```

## Wiring the knobs to the page

- **`revealMode`** — read by `<Fingerprint>` + the `[reveal value]`
  button. `eye` shows the toggle next to the fingerprint; `hover`
  removes the toggle and reveals on `pointerdown` for the duration
  of the hold; `never` removes the affordance entirely (no button,
  no hover).
- **`defaultSort`** — read by `<SecretsPage>` only when the URL has
  no `?sort=` param. URL wins.
- **`showVerifyCmd`** — read by `<FingerprintRow>`. False → the
  `+ verify cmd` expander button isn't rendered.
- **`voiceParagraph`** — read by every `<Page*>`. False → the serif
  paragraph between the heading band and the KV band is hidden.

A thin context (or zustand store) is fine. Keep it scoped to
`/secrets`; don't leak into the app-wide store.

## URL state vs. Tweak state

| State | Where it lives | Why |
|---|---|---|
| `focus`, `identity`, `sort` | URL params | Deep-linkable; back-button navigable. |
| `search` | local React | Searches don't deep-link; they're finders. |
| `revealMode`, `showVerifyCmd`, `voiceParagraph`, `defaultSort` | EDITMODE block | Per-install preferences. |
| Open/closed state of `+ verify cmd` expander | local React | Per-render UI noise. |

## Acceptance for this phase

- [ ] Toolbar toggle reveals the panel; toggle again hides it.
- [ ] Closing the panel via its `×` flips the toolbar toggle off.
- [ ] Changing `revealMode` to `never` removes every reveal-value
      affordance on the page immediately.
- [ ] Changing `defaultSort` updates the spine on next mount
      (i.e. on reload, with no `?sort=` param).
- [ ] Reloading the page preserves all four tweak values via the
      EDITMODE block.
- [ ] Tweaks panel chrome inherits Dispatch typography (no shadcn
      defaults bleeding through).
- [ ] The panel doesn't overlap the spine on narrow viewports
      (clamp-to-viewport already handled by the starter).
