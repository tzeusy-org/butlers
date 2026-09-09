/**
 * use-conversation-turn.guard.test.ts — dedup guard (bu-0ynlk.11).
 *
 * ChatPanel.tsx (ChatContent) and FloatingChatWidget.tsx (WidgetPanel) used
 * to each carry their own ~250-line copy of the send/stream/stop/retry
 * reducer, including a private `buildMessagePayload` helper. Both now
 * delegate to `useConversationTurn()`. This is a source-scan guard (per the
 * bead's acceptance criteria: "no duplicated reducer left — grep asserts one
 * implementation") rather than a behavior test — it protects against a
 * future edit silently reintroducing a second copy of the reducer instead of
 * extending the shared hook.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const SRC_DIR = path.resolve(__dirname, "..");

function findFiles(dir: string, predicate: (name: string) => boolean): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...findFiles(full, predicate));
    } else if (predicate(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

describe("useConversationTurn dedup guard", () => {
  it("buildMessagePayload is defined in exactly one file (the shared hook)", () => {
    const candidates = findFiles(
      SRC_DIR,
      (name) => (name.endsWith(".ts") || name.endsWith(".tsx")) && !name.endsWith(".test.tsx") && !name.endsWith(".test.ts"),
    );
    const definitionSites = candidates.filter((file) =>
      /function buildMessagePayload\(/.test(fs.readFileSync(file, "utf8")),
    );

    expect(definitionSites).toEqual([path.join(SRC_DIR, "hooks", "use-conversation-turn.ts")]);
  });

  it("ChatPanel.tsx and FloatingChatWidget.tsx both consume useConversationTurn", () => {
    const chatPanel = fs.readFileSync(path.join(SRC_DIR, "components/chat/ChatPanel.tsx"), "utf8");
    const floatingWidget = fs.readFileSync(
      path.join(SRC_DIR, "components/chat/FloatingChatWidget.tsx"),
      "utf8",
    );

    for (const source of [chatPanel, floatingWidget]) {
      expect(source).toMatch(/useConversationTurn\(/);
      // Neither file re-declares its own SSE reducer plumbing anymore.
      expect(source).not.toMatch(/consumeSseStream/);
    }
  });
});
