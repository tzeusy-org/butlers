import { afterEach, describe, expect, it, vi } from "vitest";

import { createClientMessageId } from "./message-id.ts";

afterEach(() => vi.unstubAllGlobals());

describe("createClientMessageId", () => {
  it("uses crypto.randomUUID when available", () => {
    const randomUUID = vi.fn(() => "3d6f0a10-45a1-4dab-a22b-b8a321bd4e4f");
    vi.stubGlobal("crypto", { randomUUID });

    expect(createClientMessageId()).toBe("3d6f0a10-45a1-4dab-a22b-b8a321bd4e4f");
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it("falls back to a UUIDv4 when randomUUID is unavailable", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => bytes.fill(0),
    });

    expect(createClientMessageId()).toBe("00000000-0000-4000-8000-000000000000");
  });
});
