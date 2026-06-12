// ---------------------------------------------------------------------------
// MemoryBrowser — Band 3 left column of the /memory house-ledger (bu-2ix8d.6)
//
// The left column (1.4fr of the 1.4fr/1fr Band-3 grid) carries:
//   1. the ONE search affordance (MemorySearch) — `/` focuses, Enter submits;
//   2. the register pills (Facts / Rules / Episodes) selecting the browse
//      register via the `register` URL param;
//   3. the focused register (FactsRegister / RulesRegister / EpisodesRegister)
//      in BROWSE mode, OR the grouped SearchResults in RESULTS mode.
//
// Browse vs. results is driven purely by the `q` URL param: while `q` is set the
// register pills + register are replaced by results (rows grouped under mono
// kind-headers, reusing the register row components). Clearing `q` (× or Esc)
// restores browse mode with the prior register and filters intact.
//
// The old Card/Tabs chrome and the per-tab/standalone search boxes are deleted —
// one search affordance, no card chrome on the register area.
//
// Binding docs:
// - pr/overview/memory-redesign/prompts/05-search-and-rail.md
// - pr/overview/memory-redesign/MEMORY_LANGUAGE.md §2, §3, §3d
// ---------------------------------------------------------------------------

import EpisodesRegister from "@/components/memory/EpisodesRegister";
import FactsRegister from "@/components/memory/FactsRegister";
import MemorySearch from "@/components/memory/MemorySearch";
import RulesRegister from "@/components/memory/RulesRegister";
import SearchResults from "@/components/memory/SearchResults";
import { Pill } from "@/components/ui/Pill";
import {
  type MemoryRegister,
  useMemoryUrlState,
} from "@/hooks/use-memory-url-state";

// ---------------------------------------------------------------------------
// Register pills
// ---------------------------------------------------------------------------

/** Browse-register selector pills. Labels are plural product vocabulary. */
const REGISTER_PILLS: { label: string; value: MemoryRegister }[] = [
  { label: "Facts", value: "facts" },
  { label: "Rules", value: "rules" },
  { label: "Episodes", value: "episodes" },
];

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface MemoryBrowserProps {
  /** When set, filter all queries to this butler scope. */
  butlerScope?: string;
}

// ---------------------------------------------------------------------------
// MemoryBrowser
// ---------------------------------------------------------------------------

export default function MemoryBrowser({ butlerScope }: MemoryBrowserProps) {
  const { state, setState } = useMemoryUrlState();
  const { register, q, kind } = state;

  // While a query is set the register area renders results mode; otherwise the
  // pills + focused browse register.
  const searching = q != null;

  return (
    <div className="flex flex-col gap-6">
      <MemorySearch />

      {searching ? (
        <SearchResults q={q} kind={kind} />
      ) : (
        <div className="flex flex-col gap-4">
          {/* Register pills — switch the browse register; reset paging. */}
          <div className="flex flex-wrap gap-1.5">
            {REGISTER_PILLS.map((pill) => (
              <Pill
                key={pill.value}
                selected={pill.value === register}
                onClick={() => setState({ register: pill.value, offset: 0 })}
              >
                {pill.label}
              </Pill>
            ))}
          </div>

          {register === "facts" && <FactsRegister butlerScope={butlerScope} />}
          {register === "rules" && <RulesRegister butlerScope={butlerScope} />}
          {register === "episodes" && (
            <EpisodesRegister butlerScope={butlerScope} />
          )}
        </div>
      )}
    </div>
  );
}
