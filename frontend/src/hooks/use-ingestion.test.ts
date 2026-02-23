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

  it("connectorsList returns stable key", () => {
    expect(ingestionKeys.connectorsList()).toEqual([
      "ingestion",
      "connectors-list",
    ]);
  });

  it("connectorsSummary includes period", () => {
    expect(ingestionKeys.connectorsSummary("7d")).toEqual([
      "ingestion",
      "connectors-summary",
      "7d",
    ]);
    expect(ingestionKeys.connectorsSummary("24h")).toEqual([
      "ingestion",
      "connectors-summary",
      "24h",
    ]);
    expect(ingestionKeys.connectorsSummary("30d")).toEqual([
      "ingestion",
      "connectors-summary",
      "30d",
    ]);
  });

  it("fanout includes period", () => {
    expect(ingestionKeys.fanout("7d")).toEqual(["ingestion", "fanout", "7d"]);
    expect(ingestionKeys.fanout("30d")).toEqual([
      "ingestion",
      "fanout",
      "30d",
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
    const k24 = ingestionKeys.connectorsSummary("24h");
    const k7d = ingestionKeys.connectorsSummary("7d");
    expect(k24).not.toEqual(k7d);
  });

  it("different identities produce different connectorDetail keys", () => {
    const k1 = ingestionKeys.connectorDetail("gmail", "a@x.com");
    const k2 = ingestionKeys.connectorDetail("gmail", "b@x.com");
    expect(k1).not.toEqual(k2);
  });

  it("overview and connectors tabs share connectorsList key by design", () => {
    // Both tabs call useConnectorSummaries which uses connectorsList key —
    // this ensures warm-cache reuse when switching tabs (spec §7).
    const key = ingestionKeys.connectorsList();
    expect(key[0]).toBe("ingestion");
    // Key is deterministic — no parameters → same key across callers
    expect(ingestionKeys.connectorsList()).toEqual(key);
  });
});
