import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import { decisionRefusalReason } from "@/api/decision-sets.ts";
import type { DecisionSet, DecisionSetItem, DecisionSetVerdict } from "@/api/decision-sets.ts";
import { ApiError } from "@/api/client.ts";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Time } from "@/components/ui/time";
import { useDecideDecisionSet, useDecisionSet } from "@/hooks/use-decision-set";

// ---------------------------------------------------------------------------
// DecisionSetPage — route /decision-sets/:setId
//
// Renders the server's view of one decision set version and posts the owner's ticks. It never
// decides what may be approved or what is ticked by default: `decidable`, `exact` and
// `in_default_selection` come from the server, and an unrenderable item is reviewed on its own
// approval page. A refusal re-reads the set; nothing is retried with different picks.
// ---------------------------------------------------------------------------

const SET_REASON: Record<string, string> = {
  state: "This set is no longer open.",
  expired: "This set has expired.",
  items: "A call in this set cannot be shown exactly, so none of it can be decided here.",
};

const ITEM_REASON: Record<string, string> = {
  missing: "The parked call could not be found.",
  unavailable: "The parked call could not be read right now.",
  not_pending: "This call was already decided elsewhere.",
  changed: "This call changed after the assistant parked it.",
  no_preview: "This call has no exact preview.",
};

const REFUSAL: Record<string, string> = {
  stale: "The set changed while you were reviewing it. Nothing was decided; review it again.",
  expired: "The set expired. Nothing was decided.",
  goal: "The goal is no longer running. Nothing was decided.",
  policy: "That selection is not allowed. Nothing was decided.",
  resource: "This set no longer exists. Nothing was decided.",
  unavailable: "Deciding sets is not switched on. Nothing was decided.",
};

/** What an unticked item comes to, worded from the producer's value; unknown reads neutrally. */
function untickedSentence(outcome: string): string {
  if (outcome === "left") return "Items you leave unticked stay waiting for your decision.";
  if (outcome === "rejected") return "Items you leave unticked are rejected.";
  return "Items you leave unticked are not approved.";
}

function defaultTicks(set: DecisionSet): Set<string> {
  return new Set(set.items.filter((item) => item.in_default_selection).map((i) => i.effect_id));
}

function Facts({ title, facts }: { title: string; facts: { label: string; value: string }[] }) {
  if (facts.length === 0) return null;
  return (
    <section aria-label={title} className="mt-2">
      <h4 className="text-xs font-medium text-muted-foreground">{title}</h4>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-sm">
        {facts.map((fact) => (
          <div key={fact.label} className="contents">
            <dt className="text-muted-foreground">{fact.label}</dt>
            {/* Plain text only: values are never interpreted as markup or links. */}
            <dd className="break-words whitespace-pre-wrap">{fact.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function ItemCard({
  item,
  ticked,
  onToggle,
  disabled,
  live,
}: {
  item: DecisionSetItem;
  ticked: boolean;
  onToggle: () => void;
  disabled: boolean;
  /** Deciding is switched on; when it is not, no tick is rendered at all. */
  live: boolean;
}) {
  const titleId = `decision-item-${item.effect_id}`;
  return (
    <li className="rounded border p-3" aria-labelledby={titleId}>
      <div className="flex items-start gap-3">
        {live && (
          <Checkbox
            id={`tick-${item.effect_id}`}
            aria-labelledby={titleId}
            className="h-11 w-11 md:h-4 md:w-4"
            checked={ticked}
            disabled={disabled || !item.exact}
            onCheckedChange={onToggle}
          />
        )}
        <div className="min-w-0 flex-1">
          <h3 id={titleId} className="font-medium">
            {item.position + 1}. {item.exact && item.title ? item.title : "Cannot be shown here"}
          </h3>
          {item.explicit_only && (
            <p className="text-xs text-muted-foreground">
              A rule or schedule change. It is decided only when you tick it yourself.
            </p>
          )}
          {item.exact ? (
            <>
              <Facts title="The call" facts={item.facts} />
              <Facts title="Parked by a goal (server record)" facts={item.lineage?.facts ?? []} />
              <Facts title="Assistant says (claims, not verified)" facts={item.claims} />
            </>
          ) : (
            <p className="mt-2 text-sm">
              {ITEM_REASON[item.reason ?? ""] ?? "This call cannot be shown here."}{" "}
              <Link className="underline" to={`/approvals/${item.effect_id}`}>
                Review it on its approval page
              </Link>
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

export default function DecisionSetPage() {
  const { setId = "" } = useParams<{ setId: string }>();
  const query = useDecisionSet(setId);
  const decide = useDecideDecisionSet(setId);
  // Ticks are kept per shown version and digest, so a re-read set starts from the server's
  // default selection again rather than carrying picks across versions.
  const [ticksFor, setTicksFor] = useState<{ key: string; ticks: Set<string> } | null>(null);

  const set = query.data?.set;
  const key = set ? `${set.version}:${set.digest}` : "";
  const ticks = useMemo(
    () => (set && ticksFor?.key === key ? ticksFor.ticks : set ? defaultTicks(set) : new Set<string>()),
    [set, ticksFor, key],
  );

  if (!setId) {
    return <p className="text-sm italic text-muted-foreground">Decision set not found.</p>;
  }
  if (query.isLoading) {
    return <p role="status">Loading decision set…</p>;
  }
  if (query.isError || !set) {
    const status = query.error instanceof ApiError ? query.error.status : null;
    return (
      <div className="space-y-4">
        <Link to="/approvals" className="inline-flex min-h-11 items-center text-sm underline">
        Approvals
      </Link>
        <p role="alert" className="text-sm">
          {status === 404
            ? "Decision set not found."
            : status === 503
              ? "Decision sets are not switched on."
              : "The decision set could not be loaded."}
        </p>
      </div>
    );
  }

  const toggle = (effectId: string) => {
    const next = new Set(ticks);
    if (next.has(effectId)) next.delete(effectId);
    else next.add(effectId);
    setTicksFor({ key, ticks: next });
  };

  const submit = (verdict: DecisionSetVerdict) => {
    const selected = set.items
      .filter((item) => ticks.has(item.effect_id))
      .map((item) => ({ effect_id: item.effect_id, item_digest: item.item_digest }));
    decide.mutate({ version: set.version, decision: verdict, selected });
  };

  const refusal = decide.error ? decisionRefusalReason(decide.error) : null;
  const decided = decide.data;
  const canDecide = set.decidable && set.decisions_live && !decided;
  const count = ticks.size;

  return (
    <div className="space-y-4">
      <Link
          to="/approvals"
          className="inline-flex min-h-11 items-center text-sm underline"
        >
          Approvals
        </Link>
      <h1 id="decision-set-title" className="text-lg font-semibold">
        Decision set: {set.items.length} call{set.items.length === 1 ? "" : "s"} waiting
      </h1>
      <p className="text-sm text-muted-foreground">
        Expires <Time value={set.expires_at} mode="absolute" />
      </p>

      {query.data?.sourcesDegraded.length ? (
        <p role="status" className="text-sm">
          Some approvals could not be read: {query.data.sourcesDegraded.join(", ")}.
        </p>
      ) : null}

      {set.decidable && !set.decisions_live && (
        <p role="status" className="text-sm">
          Deciding sets is not switched on. Review each call on its approval page.
        </p>
      )}

      {!set.decidable && (
        <p role="status" className="text-sm">
          {SET_REASON[set.reason ?? ""] ?? "This set cannot be decided here."} Review each call on
          its approval page.
        </p>
      )}

      <ul aria-labelledby="decision-set-title" className="space-y-3">
        {set.items.map((item) => (
          <ItemCard
            key={item.effect_id}
            item={item}
            ticked={ticks.has(item.effect_id)}
            onToggle={() => toggle(item.effect_id)}
            disabled={!canDecide || decide.isPending}
            live={set.decisions_live}
          />
        ))}
      </ul>

      {canDecide && (
        <div className="space-y-2">
          <p className="text-sm">{untickedSentence(set.unticked_outcome)}</p>
          <div className="flex gap-2">
            <Button
              disabled={count === 0 || decide.isPending}
              onClick={() => submit("approve")}
            >
              Approve {count} ticked
            </Button>
            <Button
              variant="outline"
              disabled={count === 0 || decide.isPending}
              onClick={() => submit("reject")}
            >
              Reject {count} ticked
            </Button>
          </div>
        </div>
      )}

      {refusal !== null && (
        <p role="alert" className="text-sm">
          {REFUSAL[refusal] ?? "Nothing was decided."}
        </p>
      )}
      {decide.isError && refusal === null && (
        <p role="alert" className="text-sm">
          The decision could not be sent. Nothing was decided.
        </p>
      )}
      {decided && (
        <section aria-label="Decision recorded" role="status" className="text-sm">
          <p>{decided.status === "approved" ? "Approved." : "Rejected."}</p>
          <ul>
            {decided.outcomes.map((o) => (
              <li key={o.effect_id}>
                {o.effect_id.slice(0, 8)}: {o.outcome} ({o.status})
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
