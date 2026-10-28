import { Tile, TileContent, TileDescription, TileHeader, TileTitle } from "@/components/ui/Tile"
import { QueryTile } from "@/components/ui/query-tile"
import { Skeleton } from "@/components/ui/skeleton"
import type { StoredFunctionFacts } from "@/api/types"
import { useStoredFunctionFacts } from "@/hooks/use-system"

function DefinerSearchPathSection({ facts }: { facts: StoredFunctionFacts }) {
  if (!facts.definer_check_available) return (
    <p data-testid="stored-functions-tile-definer-unavailable" className="mt-3 text-xs text-muted-foreground">
      Definer search-path observation unavailable. Authority remains unknown.
    </p>
  )
  if (!facts.unpinned_definers.length) return null
  return (
    <div data-testid="stored-functions-tile-unpinned" className="mt-3">
      <p className="text-sm text-[var(--red)]">{facts.unpinned_definers.length} unpinned definers</p>
      <ul className="space-y-1 text-xs">
        {facts.unpinned_definers.map(entry => (
          <li key={entry.function} className="font-mono">
            {entry.function} (owner {entry.owner}, search_path {entry.search_path ?? "unset"}):
            {" "}run controlled migrator with the operator maintenance prerequisite
          </li>
        ))}
      </ul>
    </div>
  )
}

function TileSkeleton() {
  return <Tile loading><TileHeader><TileTitle>Database Authority</TileTitle></TileHeader>
    <TileContent data-testid="stored-functions-tile-skeleton"><Skeleton className="h-8 w-40" /></TileContent>
  </Tile>
}

export function StoredFunctionsTile() {
  const q = useStoredFunctionFacts()
  return (
    <QueryTile q={q} title="Database Authority" description="Actual catalog vs. source authority"
      errorMessage="Could not load database authority facts." errorTestId="stored-functions-tile-error"
      loadingFallback={<TileSkeleton />}>
      {response => <AuthorityContent facts={response.data} />}
    </QueryTile>
  )
}

function AuthorityContent({ facts }: { facts: StoredFunctionFacts }) {
  const available = facts.catalog_check_available && ["conforming", "mismatch"].includes(facts.status)
  const conforming = available && facts.status === "conforming" && facts.objects_ok === true &&
    facts.functions.every(row => row.status === "conforming" && row.owner_ok && row.settings_ok && row.acl_ok)
  const failures = facts.functions.filter(row => row.status !== "conforming" ||
    !row.owner_ok || !row.settings_ok || !row.acl_ok)
  return (
    <Tile degraded={!available} className={available && !conforming ? "border-[var(--red)]/40" : undefined}>
      <TileHeader><TileTitle>Database Authority</TileTitle>
        <TileDescription>Actual catalog vs. source authority</TileDescription></TileHeader>
      <TileContent data-testid={!available ? "stored-functions-tile-unavailable" :
        conforming ? "stored-functions-tile-clean" : "stored-functions-tile-drifted"}>
        {!available ? <p className="text-sm text-muted-foreground">Database authority unavailable. This is not a clean bill of health.</p> :
          conforming ? <span data-testid="stored-functions-tile-clean-badge" className="text-sm text-[var(--green)]">
            Catalog conforms to source ({facts.functions.length} function overloads)
          </span> : <>
            <p data-testid="stored-functions-tile-drifted-badge" className="text-sm text-[var(--red)]">
              Catalog authority mismatch. Run the controlled migrator before serving.
            </p>
            {facts.objects_ok !== true && <p className="text-xs">Object ownership or privileges do not conform.</p>}
            <ul className="mt-2 space-y-1 text-xs">
              {failures.map(row => <li key={row.function} className="font-mono"
                data-testid={row.status === "missing" ? "stored-functions-tile-not-deployed-badge" : undefined}>
                {row.function}: {row.status}{!row.owner_ok ? "; owner mismatch" : ""}
                {!row.settings_ok ? "; settings mismatch" : ""}{!row.acl_ok ? "; privileges mismatch" : ""}
              </li>)}
            </ul>
          </>}
        <DefinerSearchPathSection facts={facts} />
      </TileContent>
    </Tile>
  )
}
