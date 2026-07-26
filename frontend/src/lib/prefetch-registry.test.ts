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
  getHealthBriefing: vi.fn(() => Promise.resolve({ data: { summary: "briefing" } })),
  getRule: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getMeasurementTypes: vi.fn(() => Promise.resolve({ types: [] })),
  getSession: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getSessions: vi.fn((params?: unknown) => Promise.resolve({ data: [], meta: { params } })),
  getApprovalDetail: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getIngestionEvent: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getTimeline: vi.fn((params: unknown) => Promise.resolve({ data: [], meta: { params } })),
}));

vi.mock("@/api/client", () => ({
  apiFetch: vi.fn(() => Promise.resolve({ data: { days: [] } })),
}));

import {
  getApprovalDetail,
  getButler,
  getEntity,
  getEpisode,
  getFact,
  getHealthBriefing,
  getIngestionEvent,
  getMeasurementTypes,
  getRule,
  getSession,
  getSessions,
  getTimeline,
} from "@/api/index.ts";
import { apiFetch } from "@/api/client";
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

  it("maps the plain Sidebar /sessions route to SessionsPage's initial list query", () => {
    const target = resolvePrefetchTarget("/sessions");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual(["sessions", { limit: 20 }]);
    expect(target!.staleTime).toBe(30_000);

    target!.queryFn();
    expect(getSessions).toHaveBeenCalledWith({ limit: 20 });
  });

  it("maps the plain Sidebar /health route to the deterministic measurement vocabulary, not the LLM briefing", () => {
    const target = resolvePrefetchTarget("/health");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual(["health-measurement-types"]);
    expect(target!.staleTime).toBe(30_000);

    target!.queryFn();
    expect(getMeasurementTypes).toHaveBeenCalledOnce();
    expect(getHealthBriefing).not.toHaveBeenCalled();
  });

  it("maps the plain Sidebar /spend route to SpendPage's forecast query", () => {
    const target = resolvePrefetchTarget("/spend");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual(["spend-forecast"]);
    expect(target!.staleTime).toBe(30_000);

    target!.queryFn();
    expect(apiFetch).toHaveBeenCalledWith("/spend/forecast");
  });

  it("does not map query or hash variants of Sidebar list routes to their unfiltered cache entries", () => {
    expect(resolvePrefetchTarget("/sessions?status=failed")).toBeNull();
    expect(resolvePrefetchTarget("/health#measurements")).toBeNull();
    expect(resolvePrefetchTarget("/spend?from=2026-07-01&to=2026-07-27")).toBeNull();
  });

  it("returns null instead of throwing on a malformed percent-encoded id segment", () => {
    // A lone "%" (invalid percent-encoding) makes decodeURIComponent throw a
    // URIError -- this must degrade to the same no-op as an unmapped route,
    // not propagate out of a pointer/focus handler.
    expect(() => resolvePrefetchTarget("/sessions/abc%")).not.toThrow();
    expect(resolvePrefetchTarget("/sessions/abc%")).toBeNull();
  });
});
