// ---------------------------------------------------------------------------
// MemorySearch — the one search affordance for /memory (bu-2ix8d.6)
//
// A single search band above the register pills (MEMORY_LANGUAGE.md §2, §3d,
// prompt 05 Part 1). There is exactly ONE search input on the page: the old
// per-tab search boxes and the standalone Inspect section are deleted.
//
//   / search                                                     ×
//   [All] [Facts] [Rules] [Episodes]
//
// - The input is local state; pressing `/` anywhere on the page focuses it,
//   `Esc` clears + blurs. Pressing Enter submits → writes `q` (+ `kind`) URL
//   params, which drives GET /api/memory/inspect via the register area's
//   results mode (handled by MemoryBrowser).
// - Kind pills scope the search to All / Facts / Rules / Episodes and write the
//   `kind` URL param (mapped to the inspect enum: fact|rule|episode).
// - `×` (and `Esc`) clears `q`, restoring browse mode with the prior register
//   and filters intact (the URL keeps register/validity/maturity/status).
//
// This component owns ONLY the search band (input + kind pills). The register
// area (browse vs. results) lives in MemoryBrowser, reading `q`/`kind` from URL.
//
// Binding docs:
// - (memory house-ledger redesign, graduated) prompts/05-search-and-rail.md
// - (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §2, §3d, §9 (no second box)
// ---------------------------------------------------------------------------

import { useEffect, useRef, useState } from "react";

import { Pill } from "@/components/ui/Pill";
import {
  type MemorySearchKind,
  useMemoryUrlState,
} from "@/hooks/use-memory-url-state";

// ---------------------------------------------------------------------------
// Kind pills
// ---------------------------------------------------------------------------

/**
 * Search-scope pills. `all` is the default; the others map to the inspect
 * endpoint's singular kind enum (fact / rule / episode). Display labels are the
 * plural product vocabulary (Facts / Rules / Episodes) — never metaphor nouns.
 */
const KIND_PILLS: { label: string; value: MemorySearchKind }[] = [
  { label: "All", value: "all" },
  { label: "Facts", value: "fact" },
  { label: "Rules", value: "rule" },
  { label: "Episodes", value: "episode" },
];

// ---------------------------------------------------------------------------
// MemorySearch
// ---------------------------------------------------------------------------

/**
 * The single search affordance. Renders the borderless `/`-prefixed input and
 * the kind pills; submitting writes the `q`/`kind` URL params (resetting paging)
 * so the register area switches to results mode and a reload reproduces the
 * search.
 */
export default function MemorySearch() {
  const { state, setState } = useMemoryUrlState();
  const inputRef = useRef<HTMLInputElement>(null);

  // Search text is LOCAL state (only the submitted value writes `q`). Seed it
  // from the URL so a deep-linked search shows its query text in the input.
  const [text, setText] = useState(state.q ?? "");

  // Keep the input text in sync when the URL `q` changes from elsewhere (e.g.
  // an attention-rail action navigates, or the back button clears the search).
  // This adjusts state during render off a changed value rather than in an
  // effect — the React-endorsed pattern (no cascading-render lint violation).
  const [lastQ, setLastQ] = useState(state.q);
  if (state.q !== lastQ) {
    setLastQ(state.q);
    setText(state.q ?? "");
  }

  // `/` focuses the input from anywhere on the page; ignore the keystroke when
  // the user is already typing in a field (so `/` stays literal there).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "/") return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      const typing =
        tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable;
      if (typing) return;
      e.preventDefault();
      inputRef.current?.focus();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const hasQuery = state.q != null;

  function submit() {
    const trimmed = text.trim();
    if (trimmed.length === 0) {
      // Submitting an empty query clears the search (restores browse mode).
      clear();
      return;
    }
    setState({ q: trimmed, offset: 0 });
  }

  function clear() {
    setText("");
    // Clear `q` (and reset paging) but keep register/validity/maturity/status —
    // the prior browse view is restored intact.
    setState({ q: null, offset: 0 });
  }

  function onSelectKind(kind: MemorySearchKind) {
    // Switching scope re-runs the active search under the new kind (reset
    // paging). When no search is active the pill still records the scope for
    // the next submit.
    setState({ kind, offset: 0 });
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Borderless search line: `/` prefix, hairline underline, mono. */}
      <div className="flex items-center gap-2 border-b border-[var(--border-soft)] pb-1.5">
        <span aria-hidden className="font-mono text-[11px] text-[var(--mfg)]">
          /
        </span>
        <input
          ref={inputRef}
          type="text"
          aria-label="Search memory"
          placeholder="search"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              clear();
              inputRef.current?.blur();
            }
          }}
          className="min-w-0 flex-1 bg-transparent font-mono text-[11px] text-[var(--fg)] placeholder:text-[var(--mfg)] focus:outline-none"
        />
        {hasQuery && (
          <button
            type="button"
            aria-label="Clear search"
            onClick={clear}
            className="font-mono text-[11px] text-[var(--mfg)] transition-colors hover:text-[var(--fg)]"
          >
            ×
          </button>
        )}
      </div>

      {/* Kind pills — scope the search; default `all`. */}
      <div className="flex flex-wrap gap-1.5">
        {KIND_PILLS.map((pill) => (
          <Pill
            key={pill.value}
            selected={pill.value === state.kind}
            onClick={() => onSelectKind(pill.value)}
          >
            {pill.label}
          </Pill>
        ))}
      </div>
    </div>
  );
}
