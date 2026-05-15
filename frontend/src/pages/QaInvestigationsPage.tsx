import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { useNavigate } from "react-router";

import type { QaCaseSummary, QaCasesParams } from "@/api/types";
import { CaseList } from "@/components/qa";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useButlers } from "@/hooks/use-butlers";
import { useQaCases } from "@/hooks/use-qa";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

type StateFilter =
  | "all"
  | QaCaseSummary["state"];
type SeverityFilter = "all" | QaCaseSummary["sev"];
type TimeRangeFilter = "24h" | "7d" | "30d" | "all";

const STATE_OPTIONS: { value: StateFilter; label: string }[] = [
  { value: "all", label: "All states" },
  { value: "detect", label: "Detect" },
  { value: "diagnose", label: "Diagnose" },
  { value: "pr", label: "PR open" },
  { value: "landed", label: "Landed" },
  { value: "escalated", label: "Escalated" },
];

const SEVERITY_OPTIONS: { value: SeverityFilter; label: string }[] = [
  { value: "all", label: "All severity" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

const TIME_RANGE_OPTIONS: { value: TimeRangeFilter; label: string }[] = [
  { value: "24h", label: "24H" },
  { value: "7d", label: "7D" },
  { value: "30d", label: "30D" },
  { value: "all", label: "All" },
];

function SelectFilter<TValue extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: TValue;
  options: { value: TValue; label: string }[];
  onChange: (value: TValue) => void;
}) {
  return (
    <label className="grid gap-1.5">
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value as TValue)}
        className="h-9 min-w-40 rounded-md border border-border bg-background px-3 font-mono text-[11px] uppercase tracking-[0.08em] text-foreground outline-none transition-colors focus-visible:ring-1 focus-visible:ring-ring"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function FilterSkeleton() {
  return (
    <div className="grid gap-3 sm:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => (
        <Skeleton key={index} className="h-9 w-full" />
      ))}
    </div>
  );
}

function EmptyLine() {
  return (
    <p className="py-10 text-sm italic text-muted-foreground font-[family-name:var(--font-serif,serif)]">
      Nothing matches.
    </p>
  );
}

export default function QaInvestigationsPage() {
  const navigate = useNavigate();
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>("all");
  const [timeRange, setTimeRange] = useState<TimeRangeFilter>("7d");
  const [selectedButlers, setSelectedButlers] = useState<Set<string>>(() => new Set());
  const [butlerMenuOpen, setButlerMenuOpen] = useState(false);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const butlerMenuRef = useRef<HTMLDivElement | null>(null);
  const selectedButlerValues = useMemo(() => Array.from(selectedButlers).sort(), [selectedButlers]);

  const casesParams: QaCasesParams = {
    limit,
    offset: 0,
    sev: severityFilter,
    since: timeRange,
    ...(stateFilter !== "all" ? { state: stateFilter } : {}),
    ...(selectedButlerValues.length > 0 ? { butler: selectedButlerValues } : {}),
  };

  const casesQuery = useQaCases(casesParams);
  const butlersQuery = useButlers();
  const caseRows = casesQuery.data?.data;
  const cases = useMemo(() => caseRows ?? [], [caseRows]);
  const total = casesQuery.data?.meta.total ?? 0;
  const hasMore = total > cases.length;

  const butlerOptions = useMemo(() => {
    const liveNames = butlersQuery.data?.data.map((butler) => butler.name) ?? [];
    return Array.from(new Set([...liveNames, ...cases.map((qaCase) => qaCase.butler)])).sort();
  }, [butlersQuery.data?.data, cases]);

  useEffect(() => {
    if (!butlerMenuOpen) return undefined;

    function handlePointerDown(event: MouseEvent) {
      if (!butlerMenuRef.current?.contains(event.target as Node)) {
        setButlerMenuOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setButlerMenuOpen(false);
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [butlerMenuOpen]);

  function resetLoadedWindow() {
    setLimit(PAGE_SIZE);
  }

  function handleStateChange(value: StateFilter) {
    setStateFilter(value);
    resetLoadedWindow();
  }

  function handleSeverityChange(value: SeverityFilter) {
    setSeverityFilter(value);
    resetLoadedWindow();
  }

  function handleTimeRangeChange(value: TimeRangeFilter) {
    setTimeRange(value);
    resetLoadedWindow();
  }

  function toggleButler(name: string) {
    setSelectedButlers((current) => {
      const next = new Set(current);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
    setButlerMenuOpen(false);
    resetLoadedWindow();
  }

  const butlerLabel =
    selectedButlers.size === 0
      ? "All butlers"
      : `${selectedButlers.size} butler${selectedButlers.size === 1 ? "" : "s"}`;

  return (
    <div className="space-y-5">
      <div className="sticky top-0 z-20 border-b border-border/70 bg-background/95 py-3 backdrop-blur">
        <div className="space-y-3">
          <Breadcrumbs
            items={[
              { label: "QA", href: "/qa" },
              { label: "Investigations" },
            ]}
          />
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className="text-2xl font-medium tracking-normal">Dispatch case index</h1>
              <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                Rule-separated QA cases
              </p>
            </div>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
              {cases.length} shown / {cases.length} loaded / {total} total
            </p>
          </div>
        </div>
      </div>

      <div className="sticky top-[104px] z-10 border-b border-border/70 bg-background/95 py-3 backdrop-blur">
        {casesQuery.isLoading || butlersQuery.isLoading ? (
          <FilterSkeleton />
        ) : (
          <div className="flex flex-wrap items-end gap-3">
            <SelectFilter
              label="State"
              value={stateFilter}
              options={STATE_OPTIONS}
              onChange={handleStateChange}
            />
            <SelectFilter
              label="Severity"
              value={severityFilter}
              options={SEVERITY_OPTIONS}
              onChange={handleSeverityChange}
            />
            <label className="grid gap-1.5">
              <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                Butler
              </span>
              <div className="relative" ref={butlerMenuRef}>
                <Button
                  type="button"
                  variant="outline"
                  aria-label={`Butlers: ${butlerLabel}`}
                  aria-haspopup="menu"
                  aria-expanded={butlerMenuOpen}
                  onClick={() => setButlerMenuOpen((open) => !open)}
                  className="h-9 min-w-40 justify-between font-mono text-[11px] uppercase tracking-[0.08em]"
                >
                  {butlerLabel}
                  <ChevronDown className="size-3.5" aria-hidden="true" />
                </Button>
                {butlerMenuOpen && (
                  <div
                    role="menu"
                    className="absolute left-0 top-10 z-30 w-48 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
                  >
                    <div className="px-2 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                      Butlers
                    </div>
                    <div className="my-1 h-px bg-border" />
                    {butlerOptions.map((name) => (
                      <button
                        key={name}
                        type="button"
                        role="menuitemcheckbox"
                        aria-checked={selectedButlers.has(name)}
                        onClick={() => toggleButler(name)}
                        className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left font-mono text-[11px] text-foreground outline-none hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent focus-visible:text-accent-foreground"
                      >
                        <span className="w-3 text-center" aria-hidden="true">
                          {selectedButlers.has(name) ? "x" : ""}
                        </span>
                        {name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </label>
            <SelectFilter
              label="Time range"
              value={timeRange}
              options={TIME_RANGE_OPTIONS}
              onChange={handleTimeRangeChange}
            />
          </div>
        )}
      </div>

      {casesQuery.isError ? (
        <p className="py-10 text-sm text-destructive">Failed to load QA cases.</p>
      ) : casesQuery.isLoading ? (
        <div className="space-y-3 border-b border-border/60 pb-4">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className={cn("h-14 w-full", index % 2 === 0 && "w-11/12")} />
          ))}
        </div>
      ) : cases.length === 0 ? (
        <EmptyLine />
      ) : (
        <CaseList
          cases={cases}
          selectedId={null}
          onSelect={(id) => navigate(`/qa/investigations/${id}`)}
          className="md:w-full"
        />
      )}

      {hasMore && (
        <div className="flex justify-center border-t border-border/60 pt-4">
          <Button type="button" variant="outline" onClick={() => setLimit((value) => value + PAGE_SIZE)}>
            Load more
          </Button>
        </div>
      )}
    </div>
  );
}
