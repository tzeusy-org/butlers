import { describe, expect, it } from "vitest";

import type { Message } from "@/api/types.ts";
import { reconcileConversationMessages } from "./message-reconciliation.ts";

function userMessage(id: string, content: string, conversationId = "conv-1"): Message {
  return {
    id,
    conversation_id: conversationId,
    role: "user",
    content,
    tool_calls: null,
    error: null,
    model: null,
    input_tokens: null,
    output_tokens: null,
    duration_ms: null,
    session_id: null,
    request_id: null,
    created_at: "2026-07-17T00:00:00.000Z",
  };
}

describe("reconcileConversationMessages", () => {
  it("retains an uncommitted optimistic user message through an empty server sync", () => {
    const optimistic = userMessage("optimistic-user-msg-123", "hello switchboard");

    expect(reconcileConversationMessages([], [optimistic], "conv-1")).toEqual([optimistic]);
  });

  it("appends an uncommitted optimistic user message after a stale server snapshot", () => {
    const stale = userMessage("msg-earlier", "an earlier message");
    const optimistic = userMessage("optimistic-user-msg-123", "hello switchboard");

    expect(reconcileConversationMessages([stale], [optimistic], "conv-1")).toEqual([
      stale,
      optimistic,
    ]);
  });

  it("does not carry an optimistic message into a different active conversation", () => {
    const optimistic = userMessage("optimistic-user-msg-123", "hello switchboard");

    expect(reconcileConversationMessages([], [optimistic], "conv-2")).toEqual([]);
  });

  it("replaces an optimistic user message when the server reports its client message id", () => {
    const optimistic = userMessage("optimistic-user-msg-123", "hello switchboard");
    const committed = userMessage("msg-123", "hello switchboard");

    expect(reconcileConversationMessages([committed], [optimistic], "conv-1")).toEqual([
      committed,
    ]);
  });
});
