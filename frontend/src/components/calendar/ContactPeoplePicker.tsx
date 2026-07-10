import { useMemo, useState } from "react";

import { Input } from "@/components/ui/input";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { useDebounce } from "@/hooks/use-debounce";
import { useContactSearch } from "@/hooks/use-contact-search";
import type { ContactSearchResult } from "@/api/types.ts";
import { cn } from "@/lib/utils";

/** A person linked to an event, identified by their identity-layer entity id. */
export interface SelectedPerson {
  entity_id: string;
  canonical_name: string;
}

export interface ContactPeoplePickerProps {
  /** Currently linked people (controlled). */
  value: SelectedPerson[];
  /** Called with the next selection whenever a person is added or removed. */
  onChange: (people: SelectedPerson[]) => void;
  disabled?: boolean;
  /** Typeahead debounce in ms (default 200). */
  debounceMs?: number;
}

/** Initials mark for a person chip/avatar — up to two leading name parts. */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * People autocomplete for the calendar create/edit dialog.
 *
 * Debounced typeahead over GET /api/contacts/search; matches render as
 * selectable chips and picked people become removable chips whose entity ids
 * the parent threads into the event-create payload (`entity_ids[]`).
 *
 * Degraded-mode: a failed search renders an honest "unavailable" note (never a
 * silently-empty list). A successful search with no matches renders a distinct
 * "No matches" line so the two states are never conflated.
 */
export function ContactPeoplePicker({
  value,
  onChange,
  disabled = false,
  debounceMs = 200,
}: ContactPeoplePickerProps) {
  const [query, setQuery] = useState("");
  const debounced = useDebounce(query, debounceMs);
  const search = useContactSearch(debounced, { enabled: !disabled });

  const selectedIds = useMemo(
    () => new Set(value.map((p) => p.entity_id)),
    [value],
  );

  // Never offer an already-linked person as a match.
  const results: ContactSearchResult[] = useMemo(
    () => (search.data?.results ?? []).filter((r) => !selectedIds.has(r.entity_id)),
    [search.data, selectedIds],
  );

  const trimmed = debounced.trim();
  const isSearching = trimmed.length > 0;

  function addPerson(result: ContactSearchResult) {
    if (selectedIds.has(result.entity_id)) return;
    onChange([
      ...value,
      { entity_id: result.entity_id, canonical_name: result.canonical_name },
    ]);
    // Clear the field so the next name can be typed cleanly.
    setQuery("");
  }

  function removePerson(entityId: string) {
    onChange(value.filter((p) => p.entity_id !== entityId));
  }

  return (
    <div className="space-y-2" data-testid="event-people-picker">
      <label htmlFor="event-people" className="text-sm font-medium">
        People
      </label>

      {value.length > 0 ? (
        <div className="flex flex-wrap gap-1.5" data-testid="people-selected-list">
          {value.map((person) => (
            <span
              key={person.entity_id}
              data-testid="people-selected-chip"
              className="inline-flex items-center gap-1.5 rounded-[3px] border border-[var(--border-strong)] py-0.5 pl-1 pr-1.5 font-mono text-[11px] text-[var(--fg)]"
            >
              <span
                aria-hidden="true"
                className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-[var(--accent)]/15 text-[9px] font-semibold text-[var(--fg)]"
              >
                {initials(person.canonical_name)}
              </span>
              {person.canonical_name}
              <button
                type="button"
                aria-label={`Remove ${person.canonical_name}`}
                data-testid="people-remove-chip"
                disabled={disabled}
                onClick={() => removePerson(person.entity_id)}
                className="text-[var(--mfg)] hover:text-[var(--fg)] disabled:opacity-50"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <Input
        id="event-people"
        data-testid="people-search-input"
        value={query}
        placeholder="Search people to link…"
        autoComplete="off"
        disabled={disabled}
        onChange={(event) => setQuery(event.target.value)}
      />

      {isSearching ? (
        search.isError ? (
          <div data-testid="people-search-degraded">
            <SourceDegradedNote
              label="People search"
              detail="unavailable"
              onRetry={() => void search.refetch()}
            />
          </div>
        ) : results.length > 0 ? (
          <ul
            data-testid="people-search-results"
            className="max-h-44 overflow-y-auto rounded-[4px] border border-[var(--border)] bg-[var(--bg)] py-1"
          >
            {results.map((result) => (
              <li key={result.entity_id}>
                <button
                  type="button"
                  data-testid="people-search-result"
                  disabled={disabled}
                  onClick={() => addPerson(result)}
                  className={cn(
                    "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-[var(--fg)]",
                    "hover:bg-[var(--accent)]/10 disabled:opacity-50",
                  )}
                >
                  <span
                    aria-hidden="true"
                    className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--accent)]/15 text-[10px] font-semibold"
                  >
                    {initials(result.canonical_name)}
                  </span>
                  <span className="min-w-0 truncate font-medium">
                    {result.canonical_name}
                  </span>
                  {result.matched_identifier ? (
                    <span className="ml-auto shrink-0 truncate font-mono text-[11px] text-[var(--mfg)]">
                      {result.matched_identifier.value}
                    </span>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
        ) : search.isFetching ? (
          <p className="px-1 text-xs text-[var(--mfg)]" data-testid="people-search-loading">
            Searching…
          </p>
        ) : (
          <p className="px-1 text-xs text-[var(--mfg)]" data-testid="people-search-empty">
            No matches.
          </p>
        )
      ) : null}
    </div>
  );
}
