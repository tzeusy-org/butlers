import { expect, test as base } from "@playwright/test";
import type { EducationSourceMaterial, MindMap } from "../../../src/api/types";

// Invented metadata only: no source content, credentials, or personal records.
const timestamp = "2026-01-01T00:00:00Z";
const mapId = "synthetic-annotation-map";
export const conceptLabel = "Synthetic provenance concept";
export const registeredUrl = "https://education-source.example.test/synthetic-notes";
const sources: EducationSourceMaterial[] = [
  { source_id: "synthetic-linked", title: "Synthetic linked notes", authors: [], type: "notes", url: registeredUrl, registered_at: timestamp },
  { source_id: "synthetic-offline", title: "Synthetic offline workbook", authors: [], type: "book", url: null, registered_at: timestamp },
];
const mindMap: MindMap = {
  id: mapId, title: "Synthetic annotation curriculum", root_node_id: "synthetic-concept",
  status: "active", created_at: timestamp, updated_at: timestamp, edges: [],
  nodes: [{
    id: "synthetic-concept", mind_map_id: mapId, label: conceptLabel,
    description: "Invented concept for browser annotation evidence.", depth: 0,
    mastery_score: 0.4, mastery_status: "learning", ease_factor: 2.5, repetitions: 1,
    next_review_at: null, last_reviewed_at: null, effort_minutes: null,
    created_at: timestamp, updated_at: timestamp,
    metadata: {
      concept_type: "conceptual",
      source_refs: [
        { source_id: "synthetic-linked", location: "linked chapter", provenance: "referenced", note: "Synthetic verified-location note" },
        { source_id: "synthetic-offline", location: "offline chapter", provenance: "referenced" },
        { source_id: "synthetic-linked", location: "recalled chapter", provenance: "model-recalled" },
        { source_id: null, location: "unsourced recollection" },
        { source_id: "synthetic-removed", location: "removed chapter", provenance: "referenced" },
        { source_id: "synthetic-linked", location: "unknown provenance chapter", provenance: "unrecognized-synthetic-value" },
      ],
    },
  }],
};

/** Per-test interception: neither the real backend nor source URLs are contacted. */
export const test = base.extend<{ registryAvailable: boolean }>({
  registryAvailable: [true, { option: true }],
  page: async ({ page, baseURL, registryAvailable }, runPage) => {
    const origin = new URL(baseURL!);
    if (!["localhost", "127.0.0.1"].includes(origin.hostname) || origin.port !== "4173") {
      throw new Error("Education annotation evidence requires the local Playwright preview.");
    }
    const unexpected: string[] = [];
    await page.routeWebSocket("**/*", (socket) => {
      const url = new URL(socket.url());
      if (url.host !== origin.host || url.pathname !== "/api/events/stream") unexpected.push("Unexpected WebSocket");
      socket.close(); // No connection to a real event service.
    });
    await page.route("**/*", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== origin.origin) {
        unexpected.push("External request blocked");
        return route.abort("blockedbyclient");
      }
      if (!url.pathname.startsWith("/api/")) return route.continue();
      const reply = (body: unknown, status = 200) => route.fulfill({ status, json: body });
      if (request.method() === "GET") {
        switch (url.pathname) {
          // Reuse the existing local harness's synthetic owner session only.
          case "/api/auth/owner/status":
          case "/api/auth/owner/csrf": return route.fallback();
          case "/api/settings/pricing": return reply({ data: {} });
          case "/api/butlers/switchboard/conversations": return reply({ data: [], meta: { total: 0, offset: 0, limit: 50 } });
          case "/api/settings/general": return reply({ data: { timezone: "UTC" } });
          case "/api/butlers": return reply({ data: [{ name: "education", status: "running" }] });
          case "/api/spend": return reply({ data: { total_cost_usd: 0 } });
          case "/api/approvals/metrics": return reply({ data: { total_pending: 0 } });
          case "/api/health": return reply({ status: "ok" });
          case "/api/education/mind-maps": {
            const status = url.searchParams.get("status");
            if (status === "active" || status === "draft") {
              const data = status === "active" ? [mindMap] : [];
              return reply({ data, meta: { total: data.length, offset: 0, limit: 50 } });
            }
            break;
          }
          case `/api/education/mind-maps/${mapId}`: return reply(mindMap);
          case `/api/education/mind-maps/${mapId}/frontier`: return reply([]);
          case "/api/education/quiz-responses": return reply({ data: [], meta: { total: 0, offset: 0, limit: 20 } });
          case "/api/education/curriculum-requests/latest": return reply({ receipts_available: true, receipt: null });
          case "/api/education/sources": {
            // A full-registry fetch must not accidentally satisfy this fixture.
            if (url.searchParams.get("source_ids") === "synthetic-linked,synthetic-offline,synthetic-removed") {
              return registryAvailable ? reply(sources) : reply({ error: { code: "SYNTHETIC_UNAVAILABLE", message: "Synthetic registry unavailable" } }, 503);
            }
            break;
          }
        }
      }
      unexpected.push(`${request.method()} ${url.pathname}`);
      await route.abort("blockedbyclient");
    });
    await runPage(page);
    expect(unexpected, "Every API request must be explicitly mocked; no source egress").toEqual([]);
  },
});
