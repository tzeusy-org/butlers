/**
 * Phone entry-route registry.
 *
 * The dashboard routes an owner reaches from a phone: the ones outbound phone
 * channels link to (approval pushes, OAuth/Spotify return redirects, chat
 * deep links). `phone-entry-routes.spec.ts` walks each entry at 375px.
 *
 * Add a route here when a new outbound link lands the owner on the dashboard.
 * A route that fails the walk is marked `fixme` with the bead filed for the
 * layout defect; the walk's assertions are never loosened.
 */

import type { Locator, Page } from "@playwright/test";

type Fixtures = Record<string, unknown>;

export interface PhoneEntryRoute {
  /** Test title and failure label. */
  name: string;
  /** Concrete URL the owner lands on. */
  path: string;
  /** Mock bodies keyed by API path glob, installed before navigation. */
  fixtures: Fixtures;
  /** The one action the owner came to the page to take. */
  primaryAction: (page: Page) => Locator;
  /** Bead id of the layout defect this route currently fails on, if any. */
  fixme?: string;
}

/**
 * Defect bead: the shared shell header controls and each page's action buttons
 * render below 44x44 at 375px. Remove the fixme when it lands; never loosen the walk.
 */
const TOUCH_TARGETS_BEAD = "bu-ley6ef";

const NOW = "2026-10-01T09:00:00Z";
const APPROVAL_ID = "11111111-1111-4111-8111-111111111111";
const CONVERSATION_ID = "22222222-2222-4222-8222-222222222222";

const APPROVAL_SUMMARY = {
  id: APPROVAL_ID,
  butler: "general",
  tool_name: "send_email",
  status: "pending",
  created_at: NOW,
  expires_at: "2026-10-02T09:00:00Z",
  why: "Reply to the landlord about the lease renewal.",
  blast_radius: "external",
  reversibility: "irreversible",
};

const APPROVALS_FIXTURES: Fixtures = {
  "**/api/approvals?*": {
    data: [APPROVAL_SUMMARY],
    meta: { stalled_count: 0, sources_degraded: [] },
  },
  "**/api/approvals/history*": { data: [], meta: { sources_degraded: [] } },
  "**/api/approvals/policy": { data: { timezone: "UTC" }, meta: {} },
  "**/api/approvals/unroutable": { data: [], meta: {} },
  "**/api/approvals/rules*": { data: [], meta: {} },
  "**/api/approvals/metrics": { data: [], meta: {} },
  [`**/api/approvals/${APPROVAL_ID}`]: {
    data: {
      ...APPROVAL_SUMMARY,
      title: "Send email to landlord",
      evidence: [{ type: "text", ref: "thread", note: "Lease ends 2026-11-30." }],
      proposed_action: {
        tool_name: "send_email",
        tool_args: { to: "landlord@example.com", subject: "Lease renewal" },
        agent_summary: "Confirm the renewal at the current rate.",
      },
    },
    meta: {},
  },
};

const SECRETS_FIXTURES: Fixtures = {
  "**/api/secrets/inventory**": {
    data: {
      user: [
        {
          id: "u-google-tze",
          entity_id: "tze",
          provider: "google",
          state: "ok",
          fingerprint: "sha256:7a3f9e2c",
          last_verified: "14:21 today",
          test: { ok: true, code: 200, at: "14:21 today" },
        },
      ],
      system: [],
      cli: [],
      identities: [{ entity_id: "tze", name: "Tze", role: "owner" }],
      providers: {
        google: {
          id: "google",
          label: "Google",
          glyph: "G",
          kind: "oauth",
          authority: "accounts.google.com",
          brief: "Calendar, Gmail, Drive read.",
          cadence: "on demand",
        },
      },
    },
    meta: {
      failing_count: 0,
      unverified_count: 0,
      failing_count_by_family: { cli: 0, system: 0, user: 0 },
      unverified_count_by_family: { cli: 0, system: 0, user: 0 },
    },
  },
  "**/api/secrets/breaks-catalogue**": { breaks: [] },
};

const CONVERSATION = {
  id: CONVERSATION_ID,
  butler_name: "switchboard",
  title: "Lease renewal",
  status: "active",
  created_at: NOW,
  updated_at: NOW,
  message_count: 1,
};

const CHAT_FIXTURES: Fixtures = {
  [`**/api/conversations/${CONVERSATION_ID}`]: CONVERSATION,
  "**/api/butlers/*/conversations": { data: [CONVERSATION], meta: {} },
  "**/api/butlers/*/conversations?*": { data: [CONVERSATION], meta: {} },
  [`**/api/butlers/*/conversations/${CONVERSATION_ID}/messages`]: {
    data: [
      {
        id: "33333333-3333-4333-8333-333333333333",
        conversation_id: CONVERSATION_ID,
        role: "assistant",
        content: "The landlord reply is waiting for your approval.",
        tool_calls: null,
        error: null,
        input_tokens: null,
        output_tokens: null,
        duration_ms: null,
        session_id: null,
        request_id: null,
        created_at: NOW,
      },
    ],
    meta: {},
  },
  "**/api/pricing*": { data: {}, meta: {} },
};

export const PHONE_ENTRY_ROUTES: PhoneEntryRoute[] = [
  {
    name: "approvals queue",
    path: "/approvals",
    fixtures: APPROVALS_FIXTURES,
    primaryAction: (page) => page.getByTestId("rail-item").first(),
    fixme: TOUCH_TARGETS_BEAD,
  },
  {
    name: "approval dossier",
    path: `/approvals/${APPROVAL_ID}`,
    fixtures: APPROVALS_FIXTURES,
    primaryAction: (page) => page.getByRole("button", { name: "Approve", exact: true }),
    fixme: TOUCH_TARGETS_BEAD,
  },
  {
    name: "secrets passport (OAuth return)",
    path: "/secrets?focus=u:google&toast=connected",
    fixtures: SECRETS_FIXTURES,
    primaryAction: (page) => page.locator('[data-direction-passport="true"] button').first(),
    fixme: TOUCH_TARGETS_BEAD,
  },
  {
    name: "chat conversation",
    path: `/chat/${CONVERSATION_ID}`,
    fixtures: CHAT_FIXTURES,
    primaryAction: (page) => page.getByRole("button", { name: "Send message" }),
    fixme: TOUCH_TARGETS_BEAD,
  },
];
