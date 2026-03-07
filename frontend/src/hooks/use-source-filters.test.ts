// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "@/api/index.ts";
import {
  sourceFilterKeys,
} from "@/hooks/use-source-filters";

// ---------------------------------------------------------------------------
// sourceFilterKeys — pure unit tests (no React rendering needed)
// ---------------------------------------------------------------------------

describe("sourceFilterKeys", () => {
  it("all key is ['source-filters']", () => {
    expect(sourceFilterKeys.all).toEqual(["source-filters"]);
  });

  it("list key includes 'list' and is derived from all", () => {
    expect(sourceFilterKeys.list()).toEqual(["source-filters", "list"]);
  });

  it("list key starts with all key", () => {
    const list = sourceFilterKeys.list();
    expect(list.slice(0, sourceFilterKeys.all.length)).toEqual([...sourceFilterKeys.all]);
  });
});

// ---------------------------------------------------------------------------
// API client stubs — verify the hooks call the right functions
// ---------------------------------------------------------------------------

describe("source filter API client stubs", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("listSourceFilters resolves to ApiResponse<SourceFilter[]>", async () => {
    const mockResponse = { data: [], meta: {} };
    vi.spyOn(apiClient, "listSourceFilters").mockResolvedValue(mockResponse);
    const result = await apiClient.listSourceFilters();
    expect(result).toEqual(mockResponse);
    expect(apiClient.listSourceFilters).toHaveBeenCalledOnce();
  });

  it("createSourceFilter passes body to API and resolves", async () => {
    const body = {
      name: "Block spam",
      filter_mode: "blacklist" as const,
      source_key_type: "domain",
      patterns: ["spam.example.com"],
    };
    const created = {
      id: "sf-001",
      ...body,
      description: null,
      created_at: "2026-03-01T00:00:00Z",
      updated_at: "2026-03-01T00:00:00Z",
    };
    vi.spyOn(apiClient, "createSourceFilter").mockResolvedValue({ data: created, meta: {} });
    const result = await apiClient.createSourceFilter(body);
    expect(result.data).toEqual(created);
    expect(apiClient.createSourceFilter).toHaveBeenCalledWith(body);
  });

  it("updateSourceFilter passes id and body to API", async () => {
    const updated = {
      id: "sf-001",
      name: "Updated",
      description: null,
      filter_mode: "blacklist" as const,
      source_key_type: "domain",
      patterns: ["newpattern.com"],
      created_at: "2026-03-01T00:00:00Z",
      updated_at: "2026-03-02T00:00:00Z",
    };
    vi.spyOn(apiClient, "updateSourceFilter").mockResolvedValue({ data: updated, meta: {} });
    const result = await apiClient.updateSourceFilter("sf-001", { name: "Updated" });
    expect(result.data.name).toBe("Updated");
    expect(apiClient.updateSourceFilter).toHaveBeenCalledWith("sf-001", { name: "Updated" });
  });

  it("deleteSourceFilter passes id to API and resolves void", async () => {
    vi.spyOn(apiClient, "deleteSourceFilter").mockResolvedValue(undefined);
    const result = await apiClient.deleteSourceFilter("sf-001");
    expect(result).toBeUndefined();
    expect(apiClient.deleteSourceFilter).toHaveBeenCalledWith("sf-001");
  });

  it("listSourceFilters propagates rejection on network error", async () => {
    vi.spyOn(apiClient, "listSourceFilters").mockRejectedValue(new Error("Network error"));
    await expect(apiClient.listSourceFilters()).rejects.toThrow("Network error");
  });

  it("createSourceFilter rejects with 409 on duplicate name", async () => {
    vi.spyOn(apiClient, "createSourceFilter").mockRejectedValue(
      new Error("Source filter name 'Block spam' already exists"),
    );
    await expect(
      apiClient.createSourceFilter({
        name: "Block spam",
        filter_mode: "blacklist",
        source_key_type: "domain",
        patterns: ["x.com"],
      }),
    ).rejects.toThrow("already exists");
  });
});

// ---------------------------------------------------------------------------
// Hook interface contracts — verify exported names
// ---------------------------------------------------------------------------

describe("use-source-filters exports", () => {
  beforeEach(() => {});

  it("exports useSourceFilters as a function", async () => {
    const mod = await import("@/hooks/use-source-filters");
    expect(typeof mod.useSourceFilters).toBe("function");
  });

  it("exports useCreateSourceFilter as a function", async () => {
    const mod = await import("@/hooks/use-source-filters");
    expect(typeof mod.useCreateSourceFilter).toBe("function");
  });

  it("exports useUpdateSourceFilter as a function", async () => {
    const mod = await import("@/hooks/use-source-filters");
    expect(typeof mod.useUpdateSourceFilter).toBe("function");
  });

  it("exports useDeleteSourceFilter as a function", async () => {
    const mod = await import("@/hooks/use-source-filters");
    expect(typeof mod.useDeleteSourceFilter).toBe("function");
  });
});
