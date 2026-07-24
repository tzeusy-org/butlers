/**
 * Tests for the route -> prefetch-target map (bu-qvnce.14 slice 4, deferred
 * from PR #2927). Covers mapped routes plus the unmapped no-op contract
 * usePrefetchOnIntent relies on.
 */

import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/index.ts", () => ({
  getButler: vi.fn((name: string) => Promise.resolve({ data: { name } })),
  getEntity: vi.fn((id: string, params?: unknown) => Promise.resolve({ data: { id, params } })),
  getEpisode: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getFact: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getRule: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getSession: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getApprovalDetail: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getIngestionEvent: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getTimeline: vi.fn((params: unknown) => Promise.resolve({ data: [], meta: { params } })),
}));

import {
  getApprovalDetail,
  getButler,
  getEntity,
  getEpisode,
  getFact,
  getIngestionEvent,
  getRule,
  getSession,
  getTimeline,
} from "@/api/index.ts";
import { POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";
import { resolvePrefetchTarget } from "./prefetch-registry";

describe("resolvePrefetchTarget", () => {
  it("maps /sessions/:id to the SAME query key SessionDetailPage uses (useGlobalSessionDetail)", () => {
    const target = resolvePrefetchTarget("/sessions/abc-123");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual(["session-detail-global", "abc-123"]);
    expect(target!.staleTime).toBe(POLL_BUS_RECONCILE_MS);

    target!.queryFn();
    expect(getSession).toHaveBeenCalledWith("abc-123");
  });

  it("maps /approvals/:id to ApprovalsPage's Dossier query key", () => {
    const target = resolvePrefetchTarget("/approvals/appr-9");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual(["approvals", "detail", "appr-9"]);

    target!.queryFn();
    expect(getApprovalDetail).toHaveBeenCalledWith("appr-9");
  });

  it("maps /butlers/:name to ButlerDetailPage's primary query", () => {
    const target = resolvePrefetchTarget("/butlers/general");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual(["butlers", "general"]);
    expect(target!.staleTime).toBe(30_000);

    target!.queryFn();
    expect(getButler).toHaveBeenCalledWith("general");
  });

  it("maps /entities/:entityId to EntityDetailPage's initial detail query", () => {
    const target = resolvePrefetchTarget("/entities/entity-42");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual([
      "memory-entity",
      "entity-42",
      { facts_limit: 200 },
    ]);
    expect(target!.staleTime).toBe(30_000);

    target!.queryFn();
    expect(getEntity).toHaveBeenCalledWith("entity-42", { facts_limit: 200 });
  });

  it("does not mistake an entities sub-route for an entity detail id", () => {
    expect(resolvePrefetchTarget("/entities/index")).toBeNull();
  });

  it("maps memory detail routes to the query keys their pages consume", () => {
    const fact = resolvePrefetchTarget("/memory/facts/fact-1");
    const episode = resolvePrefetchTarget("/memory/episodes/episode-2");
    const rule = resolvePrefetchTarget("/memory/rules/rule-3");

    expect(fact?.queryKey).toEqual(["memory-fact", "fact-1"]);
    expect(episode?.queryKey).toEqual(["memory-episode", "episode-2"]);
    expect(rule?.queryKey).toEqual(["memory-rule", "rule-3"]);

    fact!.queryFn();
    episode!.queryFn();
    rule!.queryFn();
    expect(getFact).toHaveBeenCalledWith("fact-1");
    expect(getEpisode).toHaveBeenCalledWith("episode-2");
    expect(getRule).toHaveBeenCalledWith("rule-3");
  });

  it("maps /timeline to the head-page query key (matches useTimelineLedger)", () => {
    const target = resolvePrefetchTarget("/timeline");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual(["timeline", { limit: 50 }]);

    target!.queryFn();
    expect(getTimeline).toHaveBeenCalledWith({ limit: 50 });
  });

  it("maps an ingestion drawer URL to the EventDrawer detail query", () => {
    const target = resolvePrefetchTarget("/ingestion?event=req-123");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual(["ingestion", "events", "req-123", "detail"]);
    expect(target!.staleTime).toBe(POLL_BUS_RECONCILE_MS);

    target!.queryFn();
    expect(getIngestionEvent).toHaveBeenCalledWith("req-123");
  });

  it("strips query-string/hash before matching", () => {
    const target = resolvePrefetchTarget("/sessions/abc-123?foo=bar#frag");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual(["session-detail-global", "abc-123"]);
  });

  it("decodes a URL-encoded id segment", () => {
    const target = resolvePrefetchTarget(`/approvals/${encodeURIComponent("a b")}`);
    expect(target!.queryKey).toEqual(["approvals", "detail", "a b"]);
  });

  it("returns null for an unmapped route", () => {
    expect(resolvePrefetchTarget("/not-a-route")).toBeNull();
  });

  it("returns null for /timeline sub-paths that aren't the exact list route", () => {
    expect(resolvePrefetchTarget("/timeline/foo")).toBeNull();
  });

  it("returns null for the bare /sessions list route (no id segment)", () => {
    expect(resolvePrefetchTarget("/sessions")).toBeNull();
  });

  it("returns null instead of throwing on a malformed percent-encoded id segment", () => {
    // A lone "%" (invalid percent-encoding) makes decodeURIComponent throw a
    // URIError -- this must degrade to the same no-op as an unmapped route,
    // not propagate out of a pointer/focus handler.
    expect(() => resolvePrefetchTarget("/sessions/abc%")).not.toThrow();
    expect(resolvePrefetchTarget("/sessions/abc%")).toBeNull();
  });
});
