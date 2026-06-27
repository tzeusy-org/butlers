// ---------------------------------------------------------------------------
// EpisodeDetailPage — the episode's editorial detail page. (bu-2ix8d.7)
//
// Shares the DetailSkeleton shape with the fact and rule pages. Episode-specific
// pieces:
//   - The heading is the first line of content; full content renders below in a
//     readable sans measure (~65ch) — the detail page is where the body lives.
//   - Session id (mono) linking to the session log page when present.
//   - Importance, retention class, and the consolidation glyph + WORD in mono
//     (`◦ pending`). The detail page is the one place the glyph gets its word.
//   - Provenance: facts derived from this episode (GET /facts?source_episode_id
//     — bu-awo8k.6, LIVE). The section is OMITTED when no facts were derived
//     (list nothing rather than fake it).
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
  DetailHeading,
  DetailSkeleton,
  KVBand,
  MetadataBlock,
  ProvenanceLink,
  ProvenanceSection,
  StateLine,
} from "@/components/memory/DetailSkeleton";
import { Mono } from "@/components/ui/Mono";
import { Voice } from "@/components/ui/Voice";
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

  if (!episode) {
    return (
      <DetailSkeleton backHref="/memory?register=episodes" backLabel="daybook">
        <Voice variant="italic" className="py-6 text-[var(--mfg)]">
          {isLoading ? "Turning to the daybook…" : "This episode is not in the daybook."}
        </Voice>
      </DetailSkeleton>
    );
  }

  const status = episode.consolidation_status;
  const isDead = status === "dead_letter" || status === "failed";

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
    <DetailSkeleton backHref="/memory?register=episodes" backLabel="daybook">
      <DetailEyebrow kind="episode" id={episode.id} />

      {/* Heading: the episode's opening line; the session reference is the
          record-identity subtitle below it (per the detail-page archetype —
          a session-scoped record derives identity from its session). Falls
          back to the truncated episode id when the episode has no session, so
          the record-identity line is never absent (per the domain-pages spec). */}
      <DetailHeading
        subtitle={
          episode.session_id ? `session ${episode.session_id}` : `Episode ${episode.id.slice(0, 8)}`
        }
      >
        {heading}
      </DetailHeading>

      {/* State line — consolidation state + butler, in the API's words. */}
      <StateLine fragments={[status, episode.butler ? `${episode.butler} lane` : null]} />

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
              <Mono className={cn(isDead && "text-[var(--red)]")}>
                {consolidationGlyph(status)} {status}
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
    </DetailSkeleton>
  );
}
