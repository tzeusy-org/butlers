import { Link, useParams } from "react-router";
import { Page } from "@/components/ui/page";
import { Section } from "@/components/ui/Section";
import { useGoal } from "@/hooks/use-goals";
import { approvalHref, goalStateLabel, regionHref, TERMINAL_GOAL_STATES } from "@/lib/d19";
import type { GoalDetail } from "@/api/goals";
import { Facts, GoalDoor, Read } from "./shared";

function GoalEvidence({ g }: { g: GoalDetail }) {
  const b = g.budget;
  return <div className="space-y-8">
    <p className="text-sm">{goalStateLabel(g.state)} · {g.origin}</p>
    {!TERMINAL_GOAL_STATES.has(g.state) && <p role="status">Stop unavailable. A dashboard Stop service is not available. This goal has not been stopped by this page.</p>}
    <p className="text-sm text-muted-foreground">Stop cannot undo a dispatched or committed action. Unknown effects require reconciliation.</p>
    <Section eyebrow="Summary"><Facts facts={[
      ["Goal", g.id], ["Observed ledger update", g.updated_at], ["Origin", g.origin],
      ["Exposure", g.exposed ? "Exposed" : "Unexposed"],
      ["Admitted exposure", g.admitted_exposed ? "Exposed at admission" : "Unexposed at admission"],
      ["Exposure explanation", g.exposure_causes.length ? g.exposure_causes.join(", ") : "Explanation unavailable"],
      ["Causal depth", g.chain_depth],
      ["Causing goal", g.caused_by_goal ? <GoalDoor id={g.caused_by_goal} /> : "No stored causing goal"],
      ["Causing effect", g.caused_by_effect || "No stored causing effect"],
      ["Regions", g.regions.length ? <div className="flex flex-wrap gap-4">{g.regions.map((r) => <Link className="inline-flex min-h-11 items-center underline" key={r} to={regionHref(r)}>{r}</Link>)}</div> : "No recorded regions"],
    ]} /></Section>
    <Section eyebrow="Invocation tree">
      {g.invocations.length === 0 && <p>No recorded invocations.</p>}
      <ul className="divide-y divide-border">{g.invocations.map((i) => <li id={`invocation-${i.id}`} key={i.id} className="py-3">
        <details><summary className="break-words text-sm">{i.role} · depth {i.depth} · {i.status} · <span className="tnum font-mono">{i.id}</span></summary>
          <Facts facts={[["Parent", i.parent_invocation_id || "No parent"], ["Exposure", i.exposed ? "Exposed" : "Unexposed"],
            ["Exposure cause", i.exposure_cause || "Explanation unavailable"], ["Turns used / limit", `${i.used_turns} / ${i.max_turns}`],
            ["Tokens used / limit", `${i.used_tokens} / ${i.max_tokens}`], ["Started", i.started_at || "Not recorded"],
            ["Ended", i.ended_at || "Not recorded"], ["End reason", i.end_reason || "Not recorded"]]} />
          <p>Model selection and effective prompt receipts unavailable from this read.</p>
        </details>
      </li>)}</ul>
    </Section>
    <Section eyebrow="Effects and approvals">
      {g.effects.length === 0 && <p>No recorded effects.</p>}
      <ul className="divide-y divide-border">{g.effects.map((e) => <li id={`effect-${e.id}`} key={e.id} className="py-3">
        <details><summary className="break-words text-sm">{e.capability} · {e.status} · <span className="tnum font-mono">{e.id}</span></summary>
          {e.status === "unknown" && <p>Unknown outcome. Retry and replay are unavailable; commitment has not been proved.</p>}
          {e.status === "approved" && <p>Approved, awaiting dispatch. Approval is not execution.</p>}
          {e.status === "cancelled" && <p>Cancelled. Read-only evidence; this effect cannot be approved or dispatched here.</p>}
          <Facts facts={[["Invocation", <a className="underline" href={`#invocation-${e.invocation_id}`}>{e.invocation_id}</a>],
            ["Region", <Link className="underline" to={regionHref(e.region)}>{e.region}</Link>],
            ["Approval reference", e.approval_ref ? <Link className="inline-flex min-h-11 items-center underline" to={approvalHref(e.approval_ref)}>{e.approval_ref}</Link> : "No approval reference"],
            ["Commitment receipt", e.receipt_ref || "No commitment receipt"], ["Delivery reference", e.delivery_ref || "No delivery reference"],
            ["Observed update", e.updated_at]]} />
          {g.approvals.filter((a) => a.effect_id === e.id).flatMap((a) => a.caused_goals).map((id) => <GoalDoor key={id} id={id} />)}
        </details>
      </li>)}</ul>
    </Section>
    <Section eyebrow="Budget and outcome">
      {b ? <Facts facts={[
        ["Invocations · used / reserved / limit", `${b.used_invocations} / ${b.reserved_invocations} / ${b.max_invocations}`],
        ["Children · active / limit", `${b.active_children} / ${b.max_children}`],
        ["Calls · used / reserved / limit", `${b.used_calls} / ${b.reserved_calls} / ${b.max_calls}`],
        ["Tokens · used / reserved / limit", `${b.used_tokens} / ${b.reserved_tokens} / ${b.max_tokens}`],
        ["Spend · used / reserved / limit", `${b.used_spend} / ${b.reserved_spend} / ${b.max_spend}`],
        ["Deadline", b.deadline],
      ]} /> : <p>Budget unavailable. No budget receipt was supplied.</p>}
      {g.goal_outcome ? <Facts facts={[["Lead outcome", g.goal_outcome.outcome], ["Final status", g.goal_outcome.final_status || "Unresolved"],
        ["Delivery failure", g.goal_outcome.delivery_failure || "No recorded failure"],
        ["Reply effect", g.goal_outcome.reply_effect_id ? <a className="underline" href={`#effect-${g.goal_outcome.reply_effect_id}`}>{g.goal_outcome.reply_effect_id}</a> : "No reply effect recorded"],
        ["Resolved at", g.goal_outcome.resolved_at || "Unresolved"]]} /> : <p>No recorded outcome.</p>}
      <p className="text-sm text-muted-foreground">Remaining budget, limiting dimension, model pricing and delivered-reply verification are unavailable from this projection. A reference alone does not prove delivery.</p>
    </Section>
    <Section eyebrow="Research"><p>Research-call ledger unavailable. Recorded questions, opened URLs, citations and coverage require the research read service.</p></Section>
  </div>;
}
export default function GoalPage() {
  const { goalId = "" } = useParams();
  const q = useGoal(goalId);
  return <Page title="Goal" archetype="detail"><Read q={q} label="goal detail">{({ data }) => <GoalEvidence g={data} />}</Read></Page>;
}
