// ---------------------------------------------------------------------------
// ButlerGeneralEntitiesTab — bu-nfmci
//
// Entities tab for the General butler detail page. Wires the shared
// EntityBrowser component (frontend/src/components/general/EntityBrowser.tsx)
// to the General butler's collections/entities API so the searchable,
// filterable JSONB-entity browsing experience required by the
// dashboard-domain-pages spec ("Entity browser for general butler data") is
// actually reachable, at /butlers/general?tab=entities.
//
// Data:
//   - useGeneralCollections() → /api/general/collections (dropdown options)
//   - useGeneralEntities()    → /api/general/entities (filtered table rows)
//
// Notes:
//   - EntityBrowser's collection filter operates on collection *id* (Select
//     value), but the backend /api/general/entities?collection= filter
//     matches by collection *name*. This tab resolves id -> name before
//     querying.
//   - There is no backend "distinct tags" endpoint, so the tag filter
//     dropdown is populated from a separate, higher-limit unfiltered fetch
//     rather than the (possibly already tag-filtered) visible page.
// ---------------------------------------------------------------------------

import { useMemo, useState } from "react";

import EntityBrowser from "@/components/general/EntityBrowser.tsx";
import { useGeneralCollections, useGeneralEntities } from "@/hooks/use-general";

/** Sentinel emitted by EntityBrowser's Select components for "no filter". */
const ALL_SENTINEL = "__all__";

/** Page size for the filtered entities table. */
const ENTITIES_PAGE_SIZE = 50;

/** Upper bound for the collections dropdown and the tag-discovery fetch. */
const DROPDOWN_FETCH_LIMIT = 200;

export default function ButlerGeneralEntitiesTab() {
  const [search, setSearch] = useState("");
  const [activeCollection, setActiveCollection] = useState("");
  const [activeTag, setActiveTag] = useState("");

  const { data: collectionsResp } = useGeneralCollections({
    limit: DROPDOWN_FETCH_LIMIT,
  });
  const collections = useMemo(() => collectionsResp?.data ?? [], [collectionsResp]);

  // EntityBrowser's collection Select emits the collection id; the backend
  // entities filter matches by collection name.
  const activeCollectionName = useMemo(() => {
    if (!activeCollection || activeCollection === ALL_SENTINEL) return undefined;
    return collections.find((c) => c.id === activeCollection)?.name;
  }, [activeCollection, collections]);

  const activeTagValue =
    activeTag && activeTag !== ALL_SENTINEL ? activeTag : undefined;

  const { data: entitiesResp, isLoading } = useGeneralEntities({
    q: search || undefined,
    collection: activeCollectionName,
    tag: activeTagValue,
    limit: ENTITIES_PAGE_SIZE,
  });
  const entities = entitiesResp?.data ?? [];

  // Separate, unfiltered fetch purely to populate the tag dropdown so it
  // doesn't shrink to only the currently-visible page's tags.
  const { data: tagSourceResp } = useGeneralEntities({ limit: DROPDOWN_FETCH_LIMIT });
  const availableTags = useMemo(() => {
    const tags = new Set<string>();
    for (const entity of tagSourceResp?.data ?? []) {
      for (const tag of entity.tags ?? []) tags.add(tag);
    }
    return Array.from(tags).sort();
  }, [tagSourceResp]);

  return (
    <div className="pt-4" data-testid="general-entities-tab">
      <EntityBrowser
        entities={entities}
        isLoading={isLoading}
        search={search}
        onSearchChange={setSearch}
        collections={collections}
        activeCollection={activeCollection}
        onCollectionFilter={setActiveCollection}
        activeTag={activeTag}
        onTagFilter={setActiveTag}
        availableTags={availableTags}
      />
    </div>
  );
}
