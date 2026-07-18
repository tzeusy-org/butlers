/**
 * Floating chat widget e2e happy path [bu-p6ey8.3].
 *
 * Opens the global floating chat widget (present on every route), sends a
 * message, and verifies both the optimistic user bubble and the persisted
 * assistant reply render. All conversation API calls are mocked via
 * page.route() — no real backend / Switchboard butler required (this e2e
 * preview server has none, see smoke.spec.ts).
 *
 * Mock strategy mirrors oauth-roundtrip.spec.ts: intercept the exact
 * endpoints the widget calls and fulfill them with fixture data —
 *   - GET  /api/butlers/switchboard/conversations         -> empty list
 *     (so the widget starts a fresh conversation rather than resuming one)
 *   - POST /api/butlers/switchboard/conversations         -> a hand-built
 *     SSE body carrying conversation_created/token/message_complete/done,
 *     matching the exact wire format documented in
 *     src/butlers/api/routers/conversations.py.
 *   - GET  /api/butlers/switchboard/conversations/{id}/messages -> the two
 *     persisted messages (user + routed-butler reply), which is what the
 *     `message_complete`-triggered query invalidation refetches.
 *
 * Prerequisites:
 *   npm run test:e2e:install  (once)
 *   npm run build && npm run preview  (or Playwright starts preview automatically)
 */

import { test, expect } from "@playwright/test";

const CONVERSATION_ID = "11111111-1111-1111-1111-111111111111";
const USER_TEXT = "Alice is child-of Bob — please record that.";
const ASSISTANT_REPLY = "Recorded: Alice child-of Bob — correct?";
const NOW_ISO = "2026-07-05T09:00:00.000Z";

test("floating chat widget: open, send, see persisted reply", async ({ page }) => {
  // GET (list) / POST (create) — same base path, different methods.
  await page.route(
    /\/api\/butlers\/switchboard\/conversations(\?.*)?$/,
    async (route) => {
      const method = route.request().method();

      if (method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: [], meta: { total: 0, limit: 20, offset: 0 } }),
        });
        return;
      }

      if (method === "POST") {
        const sseBody = [
          `event: conversation_created\ndata: ${JSON.stringify({
            conversation_id: CONVERSATION_ID,
            title: "New conversation",
          })}\n\n`,
          `event: token\ndata: ${JSON.stringify({ content: ASSISTANT_REPLY })}\n\n`,
          `event: message_complete\ndata: ${JSON.stringify({
            message_id: "22222222-2222-2222-2222-222222222222",
            model_name: null,
            input_tokens: null,
            output_tokens: null,
            duration_ms: null,
            tool_calls: [],
          })}\n\n`,
          `event: done\ndata: {}\n\n`,
        ].join("");

        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: sseBody,
        });
        return;
      }

      await route.continue();
    },
  );

  // GET messages for the created conversation — returned once the
  // message_complete-triggered invalidation refetches.
  await page.route(
    /\/api\/butlers\/switchboard\/conversations\/[^/]+\/messages(\?.*)?$/,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: [
            {
              id: "msg-1",
              conversation_id: CONVERSATION_ID,
              role: "user",
              content: USER_TEXT,
              created_at: NOW_ISO,
              session_id: null,
              model_name: null,
              input_tokens: null,
              output_tokens: null,
              duration_ms: null,
              tool_calls: null,
              error: null,
              request_id: null,
            },
            {
              id: "22222222-2222-2222-2222-222222222222",
              conversation_id: CONVERSATION_ID,
              role: "assistant",
              content: ASSISTANT_REPLY,
              created_at: NOW_ISO,
              session_id: null,
              model_name: null,
              input_tokens: null,
              output_tokens: null,
              duration_ms: null,
              tool_calls: [],
              error: null,
              request_id: null,
            },
          ],
          meta: { total: 2, limit: 50, offset: 0 },
        }),
      });
    },
  );

  await page.goto("/", { timeout: 10_000 });

  await page.getByTestId("floating-chat-trigger").click();
  await expect(page.getByTestId("floating-chat-panel")).toBeVisible();

  await page.getByPlaceholder("Type a message...").fill(USER_TEXT);
  await page.getByTitle("Send message").click();

  // Optimistic user bubble renders immediately, before any network round trip.
  await expect(page.getByText(USER_TEXT)).toBeVisible();

  // The routed-butler reply is fetched via the message_complete-triggered
  // refetch of GET .../messages — this is the "see persisted message" half
  // of the happy path.
  await expect(page.getByText(ASSISTANT_REPLY)).toBeVisible({ timeout: 10_000 });
});

// ---------------------------------------------------------------------------
// Unread-reply badge [bu-p6ey8.4]
// ---------------------------------------------------------------------------

test("floating chat widget: badges the trigger for a reply that arrived while closed, and opening clears it", async ({
  page,
}) => {
  const EXISTING_CONVERSATION_ID = "44444444-4444-4444-4444-444444444444";
  const SEEN_REPLY_AT = "2026-07-05T08:00:00.000Z"; // before the fixture's latest_assistant_reply_at

  // Pre-seed the unread-badge watermark (localStorage) BEFORE the fixture's
  // latest_assistant_reply_at for this conversation, before the app boots —
  // this stands in for "a reply landed since this was last seen" without
  // needing to wait out the real ~60s poll interval in the test.
  await page.addInitScript(
    ({ storageKey, conversationId, seenReplyAt }) => {
      window.localStorage.setItem(
        storageKey,
        JSON.stringify({ [conversationId]: seenReplyAt }),
      );
    },
    {
      storageKey: "butlers:chat-widget-last-seen-v2",
      conversationId: EXISTING_CONVERSATION_ID,
      seenReplyAt: SEEN_REPLY_AT,
    },
  );

  await page.route(/\/api\/butlers\/switchboard\/conversations(\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: [
            {
              id: EXISTING_CONVERSATION_ID,
              butler_name: "switchboard",
              title: "Existing thread",
              status: "active",
              created_at: NOW_ISO,
              updated_at: NOW_ISO,
              message_count: 2,
              routed_butler: "relationship",
              latest_assistant_reply_at: NOW_ISO, // > the seeded watermark -> badges
            },
          ],
          meta: { total: 1, limit: 20, offset: 0 },
        }),
      });
      return;
    }
    await route.continue();
  });

  await page.route(
    /\/api\/butlers\/switchboard\/conversations\/[^/]+\/messages(\?.*)?$/,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: [], meta: { total: 0, limit: 50, offset: 0 } }),
      });
    },
  );

  await page.goto("/", { timeout: 10_000 });

  await expect(page.getByTestId("chat-widget-unread-badge")).toBeVisible();

  await page.getByTestId("floating-chat-trigger").click();
  await expect(page.getByTestId("floating-chat-panel")).toBeVisible();
  await expect(page.getByTestId("chat-widget-unread-badge")).toHaveCount(0);
});
