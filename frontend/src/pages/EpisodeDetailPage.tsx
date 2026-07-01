// ---------------------------------------------------------------------------
// EpisodeDetailPage — the episode's detail page. (bu-2ix8d.7)
//
// Adopts <Page archetype="detail"> shell per the detail-page-archetype spec
// (bu-1jh6i). The shell owns breadcrumbs, the h1 title (first content line),
// description (session reference), status pill (consolidation state), and all
// loading / empty states. The page body owns Tiers 3–5:
//   - DetailEyebrow (kind + short id)
//   - State line (consolidation state + butler lane)
//   - Full content body (~65ch)
//   - KV band, metadata block
//   - Provenance section (derived facts; omitted when none)
//
// No commit footer — mutations live only on the fact page.
//
// Binding docs:
// - (memory house-ledger redesign, graduated) prompts/06-detail-pages.md "Episode" + "Provenance"
// - (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §4, §6
// ---------------------------------------------------------------------------

import { useMemo } from "react";
import { Link, useParams } from "react-router";

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
import { useEpisode, useFactsByEpisode } from "@/hooks/use-memory";
import { consolidationGlyph } from "@/lib/memory-derived";
import { cn } from "@/lib/utils";

/** `2026-06-10` local date, or null for an unparseable timestamp. */
function fmtDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** First 8 chars of an id for inline provenance labels. */
function shortFragment(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

export default function EpisodeDetailPage() {
  const { episodeId } = useParams<{ episodeId: string }>();
  const { data, isLoading } = useEpisode(episodeId);
  const episode = data?.data;

  // Facts derived from this episode (reverse provenance). The endpoint is live;
  // when no facts come back the section is omitted (never a faked chain).
  const { data: derivedResp } = useFactsByEpisode(episodeId);
  const derivedFacts = derivedResp?.data ?? [];

  // Heading = first non-empty line of content (the rest renders as the body).
  const heading = useMemo(() => {
    if (!episode) return "";
    return (
      episode.content
        .split("\n")
        .map((l) => l.trim())
        .find((l) => l.length > 0) ?? "Episode"
    );
  }, [episode]);

  // Title: first content line; truncate to 80 chars per spec.
  const title = heading.length > 80 ? heading.slice(0, 80) : heading || "Episode";

  // Session reference for description: the record-identity subtitle per the spec.
  // Falls back to the truncated episode id when the episode has no session.
  const description = episode
    ? episode.session_id
      ? `session ${episode.session_id}`
      : `Episode ${episode.id.slice(0, 8)}`
    : undefined;

  // Provenance: derived facts only. Omit the section when there are none.
  const provenance =
    derivedFacts.length > 0 ? (
      <>
        {derivedFacts.map((f) => (
          <ProvenanceLink
            key={f.id}
            to={`/memory/facts/${f.id}`}
            label={`derived fact ${shortFragment(f.id)}: ${f.subject} · ${f.predicate}`}
          />
        ))}
      </>
    ) : null;

  return (
    <Page
      archetype="detail"
      title={title}
      breadcrumbs={[{ label: "daybook", href: "/memory?register=episodes" }]}
      description={description}
      status={
        episode ? (
          <Badge variant="secondary">{episode.consolidation_status}</Badge>
        ) : undefined
      }
      loading={isLoading}
      empty={
        !episode && !isLoading
          ? {
              title: "Episode not found",
              description: "This episode is not in the daybook.",
            }
          : null
      }
    >
      {episode && (
        <div className="mx-auto flex max-w-[680px] flex-col gap-6">
          <DetailEyebrow kind="episode" id={episode.id} />

          {/* State line — consolidation state + butler, in the API's words. */}
          <StateLine fragments={[episode.consolidation_status, episode.butler ? `${episode.butler} lane` : null]} />

          {/* Full content — readable sans measure (~65ch). The body lives here. */}
          <Voice as="div" className="max-w-[65ch] whitespace-pre-wrap text-[14px] leading-relaxed">
            {episode.content}
          </Voice>

          {/* KV band — empty keys omitted. */}
          <KVBand
            entries={[
              {
                key: "session",
                value: episode.session_id ? (
                  <Link
                    to={`/sessions/${episode.session_id}`}
                    className="font-mono text-[11px] underline [text-underline-offset:3px] hover:text-[var(--fg)]"
                  >
                    {episode.session_id}
                  </Link>
                ) : null,
              },
              { key: "importance", value: <Mono>{episode.importance.toFixed(1)}</Mono> },
              {
                key: "consolidation",
                value: (
                  <Mono className={cn(
                    (episode.consolidation_status === "dead_letter" || episode.consolidation_status === "failed") && "text-[var(--red)]"
                  )}>
                    {consolidationGlyph(episode.consolidation_status)} {episode.consolidation_status}
                  </Mono>
                ),
              },
              { key: "references", value: <Mono>{episode.reference_count}</Mono> },
              { key: "created", value: <Mono>{fmtDate(episode.created_at)}</Mono> },
              { key: "last referenced", value: episode.last_referenced_at ? <Mono>{fmtDate(episode.last_referenced_at)}</Mono> : null },
              { key: "expires", value: episode.expires_at ? <Mono>{fmtDate(episode.expires_at)}</Mono> : null },
            ]}
          />

          {/* Metadata — raw bag as a mono code block; omitted when empty. */}
          <MetadataBlock metadata={episode.metadata} />

          {/* Provenance — derived facts only; omitted when none. */}
          <ProvenanceSection>{provenance}</ProvenanceSection>
        </div>
      )}
    </Page>
  );
}
