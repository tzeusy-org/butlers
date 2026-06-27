/**
 * Component tests for EpisodeDetailPage — the episode's editorial detail page
 * (bu-2ix8d.7).
 *
 * Acceptance ((memory house-ledger redesign, graduated) prompts/06-detail-pages.md "Episode"
 * + "Provenance"):
 *   - Shared skeleton: eyebrow (EPISODE · <short id>), heading = first content
 *     line, state line, KV band — exactly one <h1>, no "Details" chrome.
 *   - Full content renders below the heading; session id links to the session
 *     log; the consolidation glyph gets its WORD (`◦ pending`).
 *   - Provenance lists facts derived from this episode (GET /facts?source_
 *     episode_id is live), and the section is OMITTED when no facts derived.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import EpisodeDetailPage from "@/pages/EpisodeDetailPage";
import { useEpisode, useFactsByEpisode } from "@/hooks/use-memory";
import type { Episode, Fact } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: vi.fn(() => ({ episodeId: "ep-001" })) };
});

vi.mock("@/hooks/use-memory", () => ({
  useEpisode: vi.fn(),
  useFactsByEpisode: vi.fn(),
}));

type UseEpisodeResult = ReturnType<typeof useEpisode>;
type UseFactsByEpisodeResult = ReturnType<typeof useFactsByEpisode>;

const BASE_EPISODE: Episode = {
  id: "ep001abc-0000-0000-0000-000000000000",
  butler: "general",
  session_id: "sess-abc",
  content: "Alice mentioned she prefers tea over coffee.\nShe drinks it black.",
  importance: 7.5,
  reference_count: 3,
  consolidated: false,
  consolidation_status: "pending",
  created_at: "2025-01-01T10:00:00Z",
  last_referenced_at: "2025-01-15T12:00:00Z",
  expires_at: null,
  metadata: {},
};

function makeFact(overrides: Partial<Fact> = {}): Fact {
  return {
    id: "f1aaaaaa-0000-0000-0000-000000000000",
    subject: "Alice",
    predicate: "prefers",
    content: "tea over coffee",
    importance: 5,
    confidence: 0.9,
    decay_rate: 0,
    permanence: "stable",
    source_butler: "general",
    source_episode_id: "ep001abc-0000-0000-0000-000000000000",
    session_id: null,
    supersedes_id: null,
    entity_id: null,
    entity_name: null,
    object_entity_id: null,
    object_entity_name: null,
    validity: "active",
    scope: "global",
    reference_count: 1,
    created_at: "2025-01-01T11:00:00Z",
    last_referenced_at: null,
    last_confirmed_at: null,
    tags: [],
    metadata: {},
    ...overrides,
  };
}

function setEpisode(episode: Episode | null, opts: Partial<UseEpisodeResult> = {}) {
  vi.mocked(useEpisode).mockReturnValue({
    data: episode ? { data: episode } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseEpisodeResult);
}

function setDerivedFacts(facts: Fact[]) {
  vi.mocked(useFactsByEpisode).mockReturnValue({
    data: { data: facts, meta: { total: facts.length, offset: 0, limit: 50, has_more: false } },
  } as unknown as UseFactsByEpisodeResult);
}

function html(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <EpisodeDetailPage />
    </MemoryRouter>,
  );
}

describe("EpisodeDetailPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setDerivedFacts([]);
  });

  it("renders the editorial skeleton with a single H1 = first content line", () => {
    setEpisode(BASE_EPISODE);
    const out = html();
    expect((out.match(/<h1[^>]*>/g) ?? []).length).toBe(1);
    expect(out).toContain("Alice mentioned she prefers tea over coffee.");
    expect(out).toContain("EPISODE · EP001ABC");
    // State line in the API's words
    expect(out).toContain("pending");
    expect(out).toContain("general lane");
  });

  it("renders the record-identity subtitle (session reference) below the heading", () => {
    setEpisode(BASE_EPISODE);
    const out = html();
    expect(out).toContain("session sess-abc");
  });

  it("falls back to the truncated episode id when the episode has no session", () => {
    setEpisode({ ...BASE_EPISODE, session_id: null });
    const out = html();
    expect(out).toContain(`Episode ${BASE_EPISODE.id.slice(0, 8)}`);
  });

  it("renders the metadata as a mono code block when non-empty", () => {
    setEpisode({ ...BASE_EPISODE, metadata: { source: "telegram", chat_id: 42 } });
    const out = html();
    expect(out).toContain("METADATA");
    expect(out).toContain("<pre");
    expect(out).toContain("source");
    expect(out).toContain("telegram");
  });

  it("omits the metadata block when the bag is empty", () => {
    setEpisode({ ...BASE_EPISODE, metadata: {} });
    const out = html();
    expect(out).not.toContain("METADATA");
    expect(out).not.toContain("<pre");
  });

  it("renders the consolidation glyph WITH its word in the KV band", () => {
    setEpisode(BASE_EPISODE);
    const out = html();
    // `◦ pending` — the detail page is the one place the glyph gets its word.
    expect(out).toContain("◦ pending");
  });

  it("links the session id to the session log", () => {
    setEpisode(BASE_EPISODE);
    const out = html();
    expect(out).toContain("/sessions/sess-abc");
    expect(out).toContain("sess-abc");
  });

  it("renders importance and reference count in the KV band", () => {
    setEpisode(BASE_EPISODE);
    const out = html();
    expect(out).toContain("7.5");
    expect(out).toContain("importance");
    expect(out).toContain("references");
  });

  it("lists facts derived from this episode (reverse provenance live)", () => {
    setEpisode(BASE_EPISODE);
    setDerivedFacts([makeFact()]);
    const out = html();
    expect(out).toContain("PROVENANCE");
    expect(out).toContain("derived fact");
    expect(out).toContain("/memory/facts/f1aaaaaa-0000-0000-0000-000000000000");
  });

  it("omits the PROVENANCE section when no facts were derived (no faked chain)", () => {
    setEpisode(BASE_EPISODE);
    setDerivedFacts([]);
    const out = html();
    expect(out).not.toContain("PROVENANCE");
  });

  it("renders a not-found voice line when the episode is absent", () => {
    setEpisode(null);
    const out = html();
    expect(out).toContain("not in the daybook");
  });
});
