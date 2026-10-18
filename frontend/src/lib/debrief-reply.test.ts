import { beforeEach, describe, expect, it, vi } from "vitest";

import { createConversation } from "@/api/index.ts";
import { debriefReplyMessage, sendDebriefReply } from "@/lib/debrief-reply.ts";

vi.mock("@/api/index.ts", () => ({ createConversation: vi.fn() }));

function sse(...events: Array<[string, unknown]>): Response {
  const body = events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

beforeEach(() => vi.mocked(createConversation).mockReset());

describe("debrief reply", () => {
  it("prefixes the batch number the way the Telegram prompt numbers it", () => {
    expect(debriefReplyMessage(1, "  send Sam the deck by Fri ")).toBe("1: send Sam the deck by Fri");
    expect(debriefReplyMessage(null, "nothing agreed")).toBe("nothing agreed");
  });

  it("posts to the relationship butler and returns the created conversation id", async () => {
    vi.mocked(createConversation).mockResolvedValue(
      sse(["conversation_created", { conversation_id: "conv-1" }]),
    );

    await expect(sendDebriefReply(2, "nothing agreed")).resolves.toBe("conv-1");

    const [butler, body] = vi.mocked(createConversation).mock.calls[0];
    expect(butler).toBe("relationship");
    expect(body.message).toBe("2: nothing agreed");
    expect(body.message_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("rejects on a non-ok response", async () => {
    vi.mocked(createConversation).mockResolvedValue(new Response("no", { status: 503 }));

    await expect(sendDebriefReply(1, "x")).rejects.toThrow("HTTP 503");
  });

  it("rejects when the stream reports an error before the conversation exists", async () => {
    vi.mocked(createConversation).mockResolvedValue(sse(["error", { code: "X" }]));

    await expect(sendDebriefReply(1, "x")).rejects.toThrow("not accepted");
  });
});
