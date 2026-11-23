import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { getApprovalDetail, getApprovalsFlat } from "@/api/client";
import { Page } from "@/components/ui/page";
import { Section } from "@/components/ui/Section";
import { approvalHref, goalHref } from "@/lib/d19";
import { Coverage, Facts, Read } from "./shared";

function Dossier({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["approvals", "detail", id], queryFn: () => getApprovalDetail(id) });
  return <Read q={q} label="approval dossier">{({ data: d }) => <div className="space-y-6">
    <Facts facts={[["Approval", d.id], ["State", d.status], ["Capability", d.proposed_action.tool_name], ["Call digest", d.call_preview?.displayed_call.args_digest || "Digest unavailable"]]} />
    {d.call_preview ? <Section eyebrow="Stored exact call"><h2>{d.call_preview.title}</h2><Facts facts={d.call_preview.facts.map((f) => [f.label, f.value])} /></Section>
      : <p>Exact safe call preview unavailable. Approval is unavailable here.</p>}
    {d.goal ? <Section eyebrow="Producer lineage">
      <Link className="inline-flex min-h-11 items-center underline" to={goalHref(d.goal.goal_id)}>Producing goal {d.goal.goal_id}</Link>
      <Facts facts={d.goal.facts.map((f) => [f.label, f.value])} />
      <p>Opening or editing a parked draft preserves its producing goal. A standing rule does not authorize a park-only call.</p>
    </Section> : <p>Goal lineage unavailable for this approval. Origin is not inferred from its service or message.</p>}
    {d.status === "pending" && d.call_preview
      ? <Link className="inline-flex min-h-11 items-center underline" to={`/approvals/${encodeURIComponent(d.id)}`}>Review this exact call in Approvals</Link>
      : <p>This dossier is read-only. Cancelled, expired, rejected, dispatched and unknown effects cannot be approved or replayed here.</p>}
  </div>}</Read>;
}
export default function ApprovalsPage() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["approvals", "flat", "d19-preview"], queryFn: () => getApprovalsFlat("waiting", 25) });
  return <Page title="Approvals" archetype="workspace" description="Exact calls and their producing lineage. Decisions use the existing authorized review form.">
    {id ? <Dossier key={id} id={id} /> : <Read q={q} label="approval queue">{(response) => <>
      <Coverage sources={response.meta.sources_degraded} />
      <p className="text-sm text-muted-foreground">Waiting queue preview. <Link className="underline" to="/approvals?state=stalled">Open the stalled queue</Link> or <Link className="underline" to="/approvals">full queue and history</Link> for complete triage.</p>
      <ul className="divide-y divide-border">{response.data.map((a) => <li className="py-3" key={a.id}>
        <Link className="inline-flex min-h-11 items-center gap-3 underline" to={approvalHref(a.id)}>{a.tool_name} · {a.status} · {a.id}</Link>
      </li>)}</ul>
      {!response.data.length && !response.meta.sources_degraded?.length && <p>No waiting approvals in this queue.</p>}
    </>}</Read>}
  </Page>;
}
