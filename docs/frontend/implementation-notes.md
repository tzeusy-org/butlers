# Frontend Implementation Notes

> **Purpose:** Hard-won traps for the React dashboard: type-checking, lint gates, test mocks and library quirks.
> **Audience:** Frontend developers.
> **Prerequisites:** [Frontend](index.md).

Moved verbatim from `AGENTS.md` (Frontend traps).

- Type-check with `npx tsc -b` (what `npm run build` runs); `tsc --noEmit -p .` skips project
  references and misses errors in test files.
- `react-hooks/set-state-in-effect` is a hard lint error: keep form fields query-backed until a local
  draft exists instead of mirroring query data into state in `useEffect`.
- Test mocks: type a mocked `localStorage` store as `Record<string, string | null>`, and cast
  partial TanStack Query results `as unknown as ReturnType<typeof useFoo>` (a direct
  `UseQueryResult` cast fails on the discriminated union).
- Radix `Dialog` renders through a portal: jsdom tests query `document`, not the mounted container,
  and drive controlled inputs with the native value setter plus an `input` event.
- Recharts `Tooltip` formatters take `value: string | number | undefined`; narrower signatures fail
  the build.
- MapLibre: call `map.addSource` / `addLayer` only once `map.isStyleLoaded()` (or on `load`), or the
  map throws `Style is not done loading` and renders its failure fallback.
- `npm run lint:query-coercion` (CI `frontend` job) holds a per-file baseline: any net-new `?? []` /
  `?? 0` on a query's `.data` / `.meta` fails. Prefer an already-guarded value, then an `isError`
  guard; bump `query-coercion-baseline.json` only with justification. Run it before pushing.
- Serialise racing writes on one row with a TanStack mutation `scope: { id }` (last `mutate()`
  applies last). Test races with per-call deferred promises resolved at response time.
- A `vi.mocked(hook).mockImplementation(() => ({...}))` that builds a fresh object per call
  deterministically hangs vitest when a component effect depends on that value by reference (as
  `ChatPanel`'s `useConversationTurn` does): the effect re-fires every render and `act()` spins
  forever without yielding, so the per-test timeout never fires and the hang lands on a later test.
  Build one stable result object per branch outside the callback, as
  `mockHooksForConversationRefetchGap()` in `ChatPanel.test.tsx` does.
