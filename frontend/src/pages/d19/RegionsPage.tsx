import { lazy, Suspense } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { getButler, getButlerModules, getButlers } from "@/api/client";
import { Page } from "@/components/ui/page";
import { Section } from "@/components/ui/Section";
import { useGoalRegions } from "@/hooks/use-goals";
import { obligationTiming, regionHref } from "@/lib/d19";
import { Coverage, Facts, GoalDoor, Read } from "./shared";

const Finance = lazy(() => import("@/components/finance/ButlerFinanceFinancesTab"));
const Travel = lazy(() => import("@/components/travel/ButlerTravelTripsTab"));
const Home = lazy(() => import("@/components/home/ButlerHomeDevicesTab"));
const Lifestyle = lazy(() => import("@/components/lifestyle/ButlerLifestyleTasteTab"));
const Collections = lazy(() => import("@/components/general/ButlerGeneralCollectionsTab"));
const Entities = lazy(() => import("@/components/general/ButlerGeneralEntitiesTab"));

function DomainRecords({ region }: { region: string }) {
  const native = { finance: <Finance />, travel: <Travel />, home: <Home />,
    lifestyle: <Lifestyle />, general: <><Collections /><Entities /></> }[region];
  const destinations: Record<string, [string, string][]> = {
    health: [["Health records", "/health"], ["Measurements", "/health/measurements"]],
    education: [["Education and reviews", "/education"]],
    relationship: [["Contacts and entities", "/entities/index?has=contact"]],
    chronicler: [["Chronicles", "/chronicles"]],
  };
  return <Section eyebrow="Domain records">
    {native ? <Suspense fallback={<p role="status">Loading domain records…</p>}>{native}</Suspense>
      : (destinations[region] || []).map(([label, to]) => <Link className="inline-flex min-h-11 items-center underline" key={to} to={to}>{label}</Link>)}
    {!native && !destinations[region] && <p>No dedicated record workspace is registered for this region.</p>}
    <p><Link className="inline-flex min-h-11 items-center underline" to="/calendar">Calendar workspace</Link></p>
  </Section>;
}

function Operator({ region }: { region: string }) {
  const detail = useQuery({ queryKey: ["butlers", region], queryFn: () => getButler(region) });
  const modules = useQuery({ queryKey: ["butlers", region, "modules"], queryFn: () => getButlerModules(region) });
  return <div className="space-y-8">
    <p>This Operator projection is read-only.</p>
    <Section eyebrow="Service configuration"><Read q={detail} label="region configuration">{({ data }) => <Facts facts={[
      ["Region", data.name], ["Service state", data.status],
      ["Configured modules", data.modules.map((m) => `${m.name}: ${m.enabled ? "enabled" : "disabled"}`).join(", ") || "None configured"],
      ["Domain guidance names", data.skills.join(", ") || "No guidance names recorded"],
      ["Prompt provenance", "Source digests and drift evidence unavailable"],
      ["Capability inventory", "Effect classes, trust and schema digests unavailable from this read"],
    ]} />}</Read></Section>
    <Section eyebrow="Module health"><Read q={modules} label="module health">{({ data }) =>
      <ul className="divide-y divide-border">{data.map((m) => <li className="py-3" key={m.name}>{m.name} · {m.status} · {m.enabled ? "enabled" : "disabled"}</li>)}</ul>
    }</Read></Section>
    <Link className="inline-flex min-h-11 items-center underline" to="/system">System infrastructure</Link>
  </div>;
}

export function RegionPage() {
  const { region = "" } = useParams();
  const [params] = useSearchParams();
  const detail = useQuery({ queryKey: ["butlers", region], queryFn: () => getButler(region) });
  const summaries = useGoalRegions();
  const operator = params.get("view") === "operator";
  return <Page title={`${region} region`} archetype="workspace">
    <nav aria-label="Region view" className="flex flex-wrap gap-6 border-b border-border">
      <Link aria-current={!operator ? "page" : undefined} className="inline-flex min-h-11 items-center underline" to={regionHref(region)}>Workspace</Link>
      <Link aria-current={operator ? "page" : undefined} className="inline-flex min-h-11 items-center underline" to={`${regionHref(region)}?view=operator`}>Read-only Operator</Link>
    </nav>
    <Read q={detail} label="region identity">{({ data }) => data.type !== "butler"
      ? <p>This service is infrastructure, not a life region. <Link to="/system">Open System</Link></p>
      : <div className="space-y-8">
        <p className="max-w-[50ch] font-serif text-base">{data.description || "Domain purpose unavailable."}</p>
        {operator ? <Operator region={region} /> : <>
          <Read q={summaries} label="regional goals and obligations">{({ data: summary }) => {
            const r = summary.regions.find((item) => item.region === region);
            return <>
              <Coverage sources={summary.sources_degraded} />
              <Section eyebrow="Recent related goals">
                {r?.recent_goals.map((g) => <div className="border-b border-border" key={g.id}><GoalDoor id={g.id} state={g.state} /></div>)}
                {!r?.recent_goals.length && !summary.sources_degraded.includes("goal_ledger") && <p>No recorded participation.</p>}
              </Section>
              <Section eyebrow="Standing obligations">
                <p className="text-sm text-muted-foreground">Enabled schedules due within 72 hours; this is not the full standing-order inventory.</p>
                {r?.obligations_due.map((o) => <p className="border-b border-border py-3 text-sm" key={`${o.source}:${o.name}`}>{o.name} · {o.cron || "Cadence unavailable"} · {o.source} · {obligationTiming(o.next_run_at)} · {o.next_run_at}</p>)}
                {!r?.obligations_due.length && !summary.sources_degraded.includes(`obligations:${region}`) && <p>No upcoming obligations in this window.</p>}
                <p className="text-sm text-muted-foreground">Full standing orders, source provenance and last occurrence receipts unavailable.</p>
              </Section>
            </>;
          }}</Read>
          <DomainRecords region={region} />
        </>}
      </div>}
    </Read>
  </Page>;
}

export default function RegionsPage() {
  const q = useQuery({ queryKey: ["butlers"], queryFn: () => getButlers() });
  return <Page title="Regions" archetype="list" description="Specialized responsibilities within one assistant.">
    <Read q={q} label="regions">{({ data }) => <ul className="divide-y divide-border">{data.filter((r) => r.type === "butler").map((r) =>
      <li key={r.name} className="grid gap-2 py-4 md:grid-cols-[1fr_3fr]"><Link className="inline-flex min-h-11 items-center underline" to={regionHref(r.name)}>{r.name}</Link><p className="font-serif">{r.description || "Domain purpose unavailable."}</p></li>)}</ul>}
    </Read>
  </Page>;
}
