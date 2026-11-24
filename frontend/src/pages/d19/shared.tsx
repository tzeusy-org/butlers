import { Link, NavLink, Outlet } from "react-router";
import type { ReactNode } from "react";
import { Query } from "@/components/ui/query-boundary";
import type { UseQueryResult } from "@tanstack/react-query";
import { Skeleton } from "@/components/ui/skeleton";
import { D19_PREFIX, goalHref, goalStateLabel } from "@/lib/d19";

export function D19Layout() {
  return <div className="min-w-0 space-y-6 p-4 md:p-6 [&_a]:break-words [&_a]:underline-offset-4 [&_a:focus-visible]:outline [&_a:focus-visible]:outline-2 [&_a:focus-visible]:outline-offset-4 [&_a:focus-visible]:outline-focus [&_summary]:min-h-11 [&_summary]:cursor-pointer">
    <p className="font-mono text-xs text-muted-foreground">D19 preview · current dashboard remains the default</p>
    <nav aria-label="One assistant preview" className="flex flex-wrap gap-x-6 border-b border-border">
      {[["Overview", D19_PREFIX], ["Goals", `${D19_PREFIX}/goals`],
        ["Regions", `${D19_PREFIX}/regions`], ["Approvals", `${D19_PREFIX}/approvals`]].map(([label, to]) =>
        <NavLink key={to} to={to} end className={({ isActive }) =>
          `inline-flex min-h-11 items-center border-b-2 ${isActive ? "border-foreground" : "border-transparent text-muted-foreground"}`}>{label}</NavLink>)}
    </nav>
    <aside aria-label="Global Hold status" className="border-b border-border pb-4 text-sm text-muted-foreground">
      Hold status unavailable. Deferred admission and delivery counts unavailable. <Link className="inline-flex min-h-11 items-center underline" to={D19_PREFIX}>Global controls on Overview</Link>
    </aside>
    <Outlet />
  </div>;
}
export function Read<T>({ q, label, children }: {
  q: UseQueryResult<T, Error>; label: string; children: (data: T) => ReactNode;
}) {
  return <Query q={q} sourceLabel={label} loadingFallback={<div role="status" className="space-y-3">
    <span className="sr-only">Loading {label}…</span>
    <Skeleton className="h-4 w-2/3" /><Skeleton className="h-4 w-1/2" /><Skeleton className="h-4 w-1/3" />
  </div>}>
    {children}
  </Query>;
}
export function Coverage({ sources }: { sources?: string[] | null }) {
  return sources && sources.length > 0
    ? <p role="alert" className="text-sm text-[var(--amber-text)]">Partial coverage. Unavailable: {sources.join(", ")}. Counts and empty results are incomplete.</p>
    : null;
}
export function GoalDoor({ id, state }: { id: string; state?: string }) {
  return <Link to={goalHref(id)} className="inline-flex min-h-11 min-w-0 flex-wrap items-center gap-x-3 underline">
    <span className="tnum break-all font-mono text-xs">{id}</span>
    {state && <span className="text-sm">{goalStateLabel(state)}</span>}
  </Link>;
}
export function Facts({ facts }: { facts: [string, ReactNode][] }) {
  return <dl className="divide-y divide-border text-sm">{facts.map(([label, value]) =>
    <div key={label} className="grid min-w-0 grid-cols-1 gap-1 py-3 sm:grid-cols-[minmax(8rem,1fr)_minmax(0,3fr)]">
      <dt className="text-muted-foreground">{label}</dt><dd className="tnum min-w-0 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{value}</dd>
    </div>)}</dl>;
}
