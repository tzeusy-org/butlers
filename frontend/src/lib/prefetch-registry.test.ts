/**
 * Tests for the route -> prefetch-target map (bu-qvnce.14 slice 4, deferred
 * from PR #2927). Covers the three mapped targets (session detail, approval
 * detail, timeline head page) plus the unmapped no-op contract
 * usePrefetchOnIntent relies on.
 */

import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/index.ts", () => ({
  getSession: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getApprovalDetail: vi.fn((id: string) => Promise.resolve({ data: { id } })),
  getTimeline: vi.fn((params: unknown) => Promise.resolve({ data: [], meta: { params } })),
}));

import { getApprovalDetail, getSession, getTimeline } from "@/api/index.ts";
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

  it("maps /timeline to the head-page query key (matches useTimelineLedger)", () => {
    const target = resolvePrefetchTarget("/timeline");
    expect(target).not.toBeNull();
    expect(target!.queryKey).toEqual(["timeline", { limit: 50 }]);

    target!.queryFn();
    expect(getTimeline).toHaveBeenCalledWith({ limit: 50 });
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

  it("returns null for an unmapped route (e.g. a butler detail page)", () => {
    expect(resolvePrefetchTarget("/butlers/general")).toBeNull();
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
