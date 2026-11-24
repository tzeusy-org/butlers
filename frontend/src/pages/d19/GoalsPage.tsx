import { useSearchParams } from "react-router";
import { Page } from "@/components/ui/page";
import { Button } from "@/components/ui/button";
import { useGoals } from "@/hooks/use-goals";
import { GOAL_ORIGINS, GOAL_STATES } from "@/lib/d19";
import { Coverage, GoalDoor, Read } from "./shared";

const SELECT_FILTERS = [
  { key: "origin", options: GOAL_ORIGINS },
  { key: "state", options: GOAL_STATES },
  { key: "exposed", options: ["true", "false"] },
] as const;

export default function GoalsPage() {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(["origin", "state", "region", "exposed", "cursor"]
    .flatMap((key) => params.get(key) ? [[key, params.get(key)!]] : []));
  const q = useGoals(filters);
  function filter(key: string, value: string) {
    setParams((old) => { const next = new URLSearchParams(old); next.delete("cursor");
      if (value) next.set(key, value); else next.delete(key); return next; });
  }
  return <Page title="Goals" archetype="list" description="One ledger of admitted goals, newest first.">
    <form aria-label="Goal filters" className="flex flex-wrap gap-4" onSubmit={(e) => e.preventDefault()}>
      {SELECT_FILTERS.map(({ key, options }) => <label key={key} className="grid gap-1 text-sm">
          {key}<select aria-label={key} value={params.get(key) || ""}
            onChange={(e) => filter(key, e.target.value)} className="min-h-11 rounded border border-border bg-background px-3">
            <option value="">All</option>{options.map((o) => <option key={o}>{o}</option>)}
          </select></label>)}
      <label className="grid gap-1 text-sm">Region<input aria-label="Region" value={params.get("region") || ""}
        onChange={(e) => filter("region", e.target.value)} className="min-h-11 rounded border border-border bg-background px-3" /></label>
    </form>
    <Read q={q} label="goals">{(page) => <>
      <Coverage sources={page.sources_degraded} />
      <p className="text-sm text-muted-foreground">This page has {page.items.length} goals. No population total is supplied.</p>
      {page.items.length === 0 && !page.sources_degraded?.length && <p>No goals match these filters.</p>}
      <ul className="divide-y divide-border">{page.items.map((g) => <li key={g.id} className="grid gap-2 py-3 md:grid-cols-[minmax(0,2fr)_1fr_1fr]">
        <GoalDoor id={g.id} state={g.state} /><span className="text-sm">{g.origin} · {g.exposed ? "exposed" : "unexposed"}</span>
        <span className="text-sm text-muted-foreground">{g.regions.join(", ") || "No recorded region"}</span>
      </li>)}</ul>
      <div className="flex gap-3">
        {params.has("cursor") && <Button variant="outline" onClick={() => filter("cursor", "")}>First page</Button>}
        {page.next_cursor && <Button variant="outline" onClick={() => setParams((old) => {
          const next = new URLSearchParams(old); next.set("cursor", page.next_cursor!); return next;
        })}>Older goals</Button>}
      </div>
    </>}</Read>
  </Page>;
}
