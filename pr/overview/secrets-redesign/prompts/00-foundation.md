# 00 · Foundation

> Phase A of the implementation plan. End state: the route is wired,
> the API client functions exist, the types compile, and visiting
> `/secrets` renders an empty shell (just the page chrome + sidebar
> rail) without crashing.

## What you're building

1. **TypeScript types** for every wire shape in `HANDOFF.md` §5.3.
2. **API client functions** for the read endpoints (mutations come
   later, in the page phases).
3. **The route** `/secrets`, mounted in `frontend/src/router.tsx`.
4. **Shell page** at `frontend/src/pages/SecretsPage.tsx` that
   reads URL params and renders an empty `<Spine />` + `<Page />`
   slot. Both slots can return `null` for now.

Do not implement the spine, page renderers, or evidence atoms in this
phase. Those belong to Phases B–E.

## Step-by-step

### 1. Types — `frontend/src/api/types/secrets.ts`

Lift every shape from `HANDOFF.md` §5.3 verbatim. Don't paraphrase
field names; the wire is the spec.

Also export:

```ts
export type SecretKey =
  | { kind: 'user';   provider: string; identity: string }
  | { kind: 'system'; key: string }
  | { kind: 'cli';    id: string };

export const parseFocus  = (raw: string): SecretKey | null => { /* … */ };
export const encodeFocus = (k: SecretKey): string => { /* "u:spotify" | "s:KEY" | "c:claude-cli" */ };
```

The `focus` URL param uses the compact `u:` / `s:` / `c:` prefix —
see `secrets-data.jsx` and `secrets-passport.jsx` in the prototype.

### 2. API client — `frontend/src/api/client.secrets.ts`

```ts
export async function fetchInventory(identityId: string): Promise<Inventory> { … }
export async function fetchUserSecret  (provider: string, identityId: string): Promise<UserSecret> { … }
export async function fetchSystemSecret(key: string): Promise<SystemSecret> { … }
export async function fetchCliRuntime  (id: string): Promise<CliRuntime> { … }
export async function fetchAudit(scope: 'user' | 'system' | 'cli', key: string): Promise<AuditEvent[]> { … }
```

Wire these as react-query queries. Suggested keys:

```ts
['secrets', 'inventory', identityId]
['secrets', 'user',   provider, identityId]
['secrets', 'system', key]
['secrets', 'cli',    id]
['secrets', 'audit',  scope, key]
```

`fetchInventory` returns the canonical single round trip used on
mount. The per-credential fetches exist for deep-link refresh and
post-mutation invalidation.

### 3. Backend (if not already there)

If the endpoints in `HANDOFF.md` §5.1 don't exist, add them. They
each project from the existing tables:

- `butler_secrets` for system secrets.
- `entity_info` on the owner entity for user secrets (with the
  existing OAuth grant storage).
- Whatever CLI-runtime store exists today.

The `fingerprint` is computed server-side as
`'sha256:' + hex(sha256(value))[:8]`. Never send the value over the
wire on a list endpoint. The *reveal* mutation
(`POST /api/secrets/.../reveal`) returns the value once with a short
TTL on the response cache header.

The `breaks` array is computed from a static feature-catalogue
(`features.json` or equivalent) that maps `provider → [{butler,
feature, severity, requiredScopes}]`. On read, the server merges this
with the credential's current state to produce the array.

### 4. Route — `frontend/src/router.tsx`

```tsx
import SecretsPage from '@/pages/SecretsPage';
{
  path: '/secrets',
  element: <SecretsPage />,
}
```

Keep the old route id; the URL doesn't change.

### 5. Shell — `frontend/src/pages/SecretsPage.tsx`

```tsx
export default function SecretsPage() {
  const [params, setParams] = useSearchParams();
  const identityId = params.get('identity') ?? useCurrentUser().id;
  const focus      = params.get('focus');
  const sort       = params.get('sort') ?? 'severity';

  const { data: inventory, isLoading } = useQuery({
    queryKey: ['secrets', 'inventory', identityId],
    queryFn: () => fetchInventory(identityId),
  });

  return (
    <PageShell>
      <SecretsHeader identityId={identityId} inventory={inventory} />
      <div className="secrets-book">
        <Spine
          inventory={inventory}
          identityId={identityId}
          focus={focus}
          sort={sort}
          onChange={(next) => setParams(next, { replace: false })}
        />
        <Page focus={focus} inventory={inventory} />
      </div>
    </PageShell>
  );
}
```

For this phase, `<Spine>` and `<Page>` can be stubs that return
`null`. The header (KPI strip + identity chip + "N credentials need
attention" line) can also be a stub.

### 6. Tokens

Make sure `frontend/src/index.css` exports the Dispatch tokens that
the prototype uses. The names match `secrets-shared.jsx`'s `Cs`
object via `window.C`:

```css
:root {
  /* Surfaces */
  --bg:           oklch(0.145 0 0);
  --bg-deep:      oklch(0.115 0 0);
  --bg-elev:      oklch(0.185 0 0);
  --border:       oklch(1 0 0 / 0.10);
  --border-soft:  oklch(1 0 0 / 0.06);
  --border-strong:oklch(1 0 0 / 0.18);
  /* Foreground */
  --fg:           oklch(0.985 0 0);
  --mfg:          oklch(0.78 0 0);
  --dim:          oklch(0.50 0 0);
  /* State */
  --red:          oklch(0.70 0.18 25);
  --amber:        oklch(0.78 0.14 80);
  --green:        oklch(0.74 0.14 145);
}
```

If these already exist, do **not** override them in the secrets
folder. Use the existing tokens.

## Acceptance for this phase

- [ ] `pnpm tsc --noEmit` clean.
- [ ] Visiting `/secrets` renders the page chrome (header + empty
      book body) with no console errors.
- [ ] One network call fires: `GET /api/secrets/inventory?identity=…`.
- [ ] Sidebar item is highlighted when on `/secrets` (existing
      behaviour; just confirm it still works).
- [ ] The empty inventory case renders gracefully (no crashes if
      the API returns `{ cli: [], system: [], user: [] }`).
