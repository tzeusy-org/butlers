import { Link } from "react-router";
import { Page } from "@/components/ui/page";
import { Section } from "@/components/ui/Section";
import { useGoals, useGoalRegions } from "@/hooks/use-goals";
import { D19_PREFIX, obligationTiming, regionHref, TERMINAL_GOAL_STATES } from "@/lib/d19";
import { Coverage, GoalDoor, Read } from "./shared";

export default function OverviewPage() {
  const goals = useGoals();
  const regions = useGoalRegions();
  return <Page title="Overview" archetype="editorial" breadcrumbs={[{ label: "One assistant" }]}>
    <p className="max-w-[50ch] font-serif text-base leading-relaxed">The goal ledger brings work across specialized regions into one view.</p>
    <Section eyebrow="Completed goals today">
      <p className="text-3xl">Unavailable</p>
      <p className="text-sm text-muted-foreground">Completion-time and delivered-reply evidence is unavailable. A daily total cannot be established.</p>
    </Section>
    <div className="grid min-w-0 gap-8 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <Section eyebrow="Active goals"><Read q={goals} label="active goals">{(page) => <>
        <Coverage sources={page.sources_degraded} />
        <p className="text-sm text-muted-foreground">Active work within the newest {page.items.length} records; this is not a complete active-goal count.</p>
        <ul className="divide-y divide-border">{page.items.filter((g) => !TERMINAL_GOAL_STATES.has(g.state)).map((g) =>
          <li key={g.id} className="py-2"><GoalDoor id={g.id} state={g.state} /></li>)}</ul>
        <Link className="inline-flex min-h-11 items-center underline" to={`${D19_PREFIX}/goals`}>Browse all goals and outcomes</Link>
      </>}</Read></Section>
      <Section eyebrow="Owner decisions"><p>Waiting and stalled decisions retain their exact calls and current eligibility.</p>
        <Link className="inline-flex min-h-11 items-center underline" to={`${D19_PREFIX}/approvals`}>Open approval queue</Link>
        <p className="text-sm text-muted-foreground">Decision totals and service exceptions are not aggregated here.</p>
      </Section>
    </div>
    <Section eyebrow="Regions and due obligations"><Read q={regions} label="region obligations">{({ data }) => <>
      <Coverage sources={data.sources_degraded} />
      <p className="text-sm text-muted-foreground">Enabled schedules due within 72 hours. Coverage is independent for each region.</p>
      <ul className="divide-y divide-border">{data.regions.map((r) => <li key={r.region} className="grid gap-3 py-3 md:grid-cols-[1fr_3fr]">
        <Link className="inline-flex min-h-11 items-center underline" to={regionHref(r.region)}>{r.region}</Link>
        <ul>{r.obligations_due.map((o) => <li key={`${o.source}:${o.name}`} className="py-2 text-sm">{o.name} · {obligationTiming(o.next_run_at)} · {o.next_run_at} · {o.source}</li>)}</ul>
      </li>)}</ul>
      {data.regions.length === 0 && !data.sources_degraded.length && <p>No recorded regional goals or upcoming obligations.</p>}
    </>}</Read></Section>
    <Section eyebrow="Global controls"><p>Hold, Hold and stop all, and Release are unavailable. The authenticated global control service and its independent durable receipts are not available.</p>
      <p className="text-sm text-muted-foreground">No Hold or Stop has been requested by this page. Existing owner replies and approval controls retain their own rules.</p>
    </Section>
  </Page>;
}
