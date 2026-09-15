// ---------------------------------------------------------------------------
// RuleDetailPage — the rule's detail page. (bu-2ix8d.7)
//
// Adopts <Page archetype="detail"> shell per the detail-page-archetype spec
// (bu-1jh6i). The shell owns breadcrumbs, the h1 title (directive text),
// status pill (maturity), and all loading / empty states. The page body owns
// Tiers 3–5:
//   - DetailEyebrow (kind + short id)
//   - State line (maturity + permanence + scope)
//   - Outcome record (two mono lines)
//   - KV band, metadata block
//   - Provenance section (omitted when no source episode)
//   - Commit footer (Retire; the only rule mutation, bu-6t8ix.3)
//
// Binding docs:
// - (memory house-ledger redesign, graduated) prompts/06-detail-pages.md "Rule"
// - (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §4, §6
// ---------------------------------------------------------------------------

import { useState } from "react";
import { useParams } from "react-router";
import { toast } from "sonner";

import {
  DetailEyebrow,
  KVBand,
  MetadataBlock,
  ProvenanceLink,
  ProvenanceSection,
  StateLine,
} from "@/components/memory/DetailSkeleton";
import { Mono } from "@/components/ui/Mono";
import { Voice } from "@/components/ui/Voice";
import { Badge } from "@/components/ui/badge";
import { Page } from "@/components/ui/page";
import { useTimezone } from "@/components/ui/timezone-context";
import { useRetireRule, useRule } from "@/hooks/use-memory";
import { formatDayStamp, permanenceTag } from "@/lib/memory-derived";
import { cn } from "@/lib/utils";
import type { MemoryRule } from "@/api/types.ts";

/** First 8 chars of an id for inline provenance labels. */
function shortFragment(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

// ---------------------------------------------------------------------------
// Commit footer (Retire)
// ---------------------------------------------------------------------------

/**
 * The only mutation on the rule surface: retire (stops the rule from firing,
 * kept on the books for reference — bu-6t8ix.3). One-step confirm, mirroring
 * FactDetailPage's Retract: the pill becomes `Retire (confirm?)` for 5s, no
 * modal. Once retired there is no un-retire verb yet, so the button becomes a
 * disabled `Retired` label instead of re-arming.
 */
function CommitFooter({ rule }: { rule: MemoryRule }) {
  const retireMutation = useRetireRule();
  const [armed, setArmed] = useState(false);
  const isRetired = rule.retired_at != null;

  const onRetire = () => {
    if (isRetired) return;
    if (!armed) {
      setArmed(true);
      // Disarm after 5s if the owner does not follow through.
      window.setTimeout(() => setArmed(false), 5000);
      return;
    }
    setArmed(false);
    retireMutation.mutate(rule.id, {
      onError: (err) =>
        toast.error("Failed to retire rule", {
          description: err instanceof Error ? err.message : undefined,
        }),
    });
  };

  return (
    <footer className="flex flex-col gap-3 border-t border-[var(--border-soft)] pt-5">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={retireMutation.isPending || isRetired}
          onClick={onRetire}
          className={cn(
            "inline-flex h-7 items-center rounded-full px-3.5",
            "font-mono text-[11px] font-medium",
            "border border-[var(--border)] bg-transparent text-fg",
            "transition-colors hover:border-fg",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fg/30",
            "disabled:pointer-events-none disabled:opacity-40",
          )}
        >
          {isRetired ? "Retired" : armed ? "Retire (confirm?)" : "Retire"}
        </button>
        <Voice variant="italic" as="span" className="text-[13px] text-[var(--mfg)]">
          {isRetired
            ? "This rule no longer fires; kept on the books for reference."
            : "Stops the rule from firing; kept on the books for reference."}
        </Voice>
      </div>
    </footer>
  );
}

export default function RuleDetailPage() {
  const tz = useTimezone();
  const { ruleId } = useParams<{ ruleId: string }>();
  const { data, isLoading } = useRule(ruleId ?? null);
  const rule = data?.data;

  // Title: the directive text is the record identity; truncate to 80 chars per spec.
  const title = rule
    ? rule.content.length > 80
      ? rule.content.slice(0, 80)
      : rule.content
    : "Rule";

  const harmful = rule?.harmful_count ?? 0;

  // Forgotten (soft-deleted) rules are excluded from the register and the
  // Proven-rules KPI by default (bu-5ud8p.2), so this page is the one place
  // a forgotten rule can still be reached (direct link, ?forgotten=true
  // audit query). Label it explicitly rather than leaving it distinguishable
  // only via the raw metadata block below.
  const forgotten = rule?.metadata?.["forgotten"] === true;

  // Retired (bu-6t8ix.3): the rule stopped firing via an explicit owner
  // action (distinct from forgotten — a retired rule was not necessarily
  // wrong, it's just no longer enforced). Surfaced the same way forgotten is.
  const retired = rule?.retired_at != null;

  const provenance =
    rule?.source_episode_id != null ? (
      rule.source_episode_status === "available" ? (
        <ProvenanceLink
          to={`/memory/episodes/${rule.source_episode_id}`}
          label={`derived from episode ${shortFragment(rule.source_episode_id)}`}
        />
      ) : (
        <span className="font-mono text-[11px] text-[var(--mfg)]">
          Source {rule.source_episode_status ?? "unresolved"}
        </span>
      )
    ) : null;

  return (
    <Page
      archetype="detail"
      title={title}
      breadcrumbs={[{ label: "standing orders", href: "/memory?register=rules" }]}
      status={
        rule ? (
          <div className="flex gap-1.5">
            <Badge variant="secondary">{rule.maturity}</Badge>
            {forgotten && <Badge variant="secondary">forgotten</Badge>}
            {retired && <Badge variant="secondary">retired</Badge>}
          </div>
        ) : undefined
      }
      loading={isLoading}
      empty={
        !rule && !isLoading
          ? {
              title: "Rule not found",
              description: "This rule is not on the books.",
            }
          : null
      }
    >
      {rule && (
        <div className="mx-auto flex max-w-[680px] flex-col gap-6">
          <DetailEyebrow kind="rule" id={rule.id} />

          {/* State line — maturity + permanence + scope, in the API's words. */}
          <StateLine
            fragments={[
              rule.maturity,
              `${rule.permanence} permanence`,
              rule.scope ? `${rule.scope} scope` : null,
            ]}
          />

          {/* Outcome record — two mono lines. harmful goes --red only when > 0. */}
          <div className="flex flex-col gap-1">
            <Mono className="tabular-nums">
              applied {rule.applied_count} · helpful {rule.success_count} ·{" "}
              <span className={cn(harmful > 0 && "text-[var(--red-text)]")}>
                harmful {harmful}
              </span>{" "}
              · effectiveness {rule.effectiveness_score.toFixed(2)}
            </Mono>
            <Mono muted className="tabular-nums">
              {[
                rule.last_applied_at ? `last applied ${formatDayStamp(rule.last_applied_at, tz)}` : null,
                rule.last_evaluated_at ? `last evaluated ${formatDayStamp(rule.last_evaluated_at, tz)}` : null,
              ]
                .filter(Boolean)
                .join(" · ") || "never applied"}
            </Mono>
          </div>

          {/* KV band — empty keys omitted. */}
          <KVBand
            entries={[
              { key: "permanence", value: <span className="font-mono text-[11px] tabular-nums">{permanenceTag(rule.permanence)}</span> },
              { key: "confidence", value: <Mono>{rule.confidence.toFixed(2)}</Mono> },
              { key: "decay rate", value: <Mono>{rule.decay_rate.toFixed(3)}/day</Mono> },
              { key: "created", value: <Mono>{formatDayStamp(rule.created_at, tz)}</Mono> },
              { key: "source butler", value: rule.source_butler ? <Mono>{rule.source_butler}</Mono> : null },
              { key: "tags", value: rule.tags.length > 0 ? rule.tags.join(", ") : null },
            ]}
          />

          {/* Metadata — raw bag as a mono code block; omitted when empty. */}
          <MetadataBlock metadata={rule.metadata} />

          {/* Provenance — omitted when no source episode. */}
          <ProvenanceSection>{provenance}</ProvenanceSection>

          {/* Commit footer — the only rule mutation (Retire, bu-6t8ix.3). */}
          <CommitFooter rule={rule} />
        </div>
      )}
    </Page>
  );
}
