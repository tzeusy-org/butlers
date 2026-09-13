/**
 * Tests for use-ingestion query key factory.
 *
 * We only test the deterministic ingestionKeys factory here — the
 * hook behavior (fetch, cache, enabled flags) is covered by integration
 * patterns in the tab component tests. Testing hooks that require a live
 * QueryClient + network is covered separately; this file focuses on the
 * pure query-key contract so that tab key sharing can be verified.
 */

import { describe, expect, it } from "vitest";
import { ingestionKeys } from "./use-ingestion";

describe("ingestionKeys", () => {
  it("all returns base key", () => {
    expect(ingestionKeys.all).toEqual(["ingestion"]);
  });

  it("connectorSummaries returns stable key", () => {
    expect(ingestionKeys.connectorSummaries()).toEqual([
      "ingestion",
      "connectors-summaries",
    ]);
  });

  it("connectorDetail includes type and identity", () => {
    expect(
      ingestionKeys.connectorDetail("gmail", "user@example.com"),
    ).toEqual([
      "ingestion",
      "connector-detail",
      "gmail",
      "user@example.com",
    ]);
  });

  it("connectorStats includes type, identity, and period", () => {
    expect(
      ingestionKeys.connectorStats("telegram", "bot-123", "24h"),
    ).toEqual([
      "ingestion",
      "connector-stats",
      "telegram",
      "bot-123",
      "24h",
    ]);
  });

  it("different periods produce different keys (cache isolation)", () => {
    const k24 = ingestionKeys.connectorStats("gmail", "a@x.com", "24h");
    const k7d = ingestionKeys.connectorStats("gmail", "a@x.com", "7d");
    expect(k24).not.toEqual(k7d);
  });

  it("different identities produce different connectorDetail keys", () => {
    const k1 = ingestionKeys.connectorDetail("gmail", "a@x.com");
    const k2 = ingestionKeys.connectorDetail("gmail", "b@x.com");
    expect(k1).not.toEqual(k2);
  });

  it("Timeline, System, and connectors views share summaries key by design", () => {
    // Every connector surface calls useConnectorSummaries, so one role-aware
    // response stays warm across views (spec §7).
    const key = ingestionKeys.connectorSummaries();
    expect(key[0]).toBe("ingestion");
    // Key is deterministic — no parameters → same key across callers
    expect(ingestionKeys.connectorSummaries()).toEqual(key);
  });

});
