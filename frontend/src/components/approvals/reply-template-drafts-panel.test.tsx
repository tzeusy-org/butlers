// @vitest-environment jsdom
/**
 * Tests for <ReplyTemplateDraftsPanel> (bu-jv1moa.42): the owner's editor for
 * inactive standing-reply template drafts.
 *
 * Covers:
 * - Authoring sends a create with the shaped templates, pinned recipients and
 *   one idempotency key reused across a retry
 * - Edit sends the inspected revision as expected_revision; a stale revision
 *   (409) shows actionable copy
 * - Revoke needs an explicit confirm and sends the current revision
 * - Preview renders the verdict, humanized reasons and authority none
 * - No activation control exists anywhere in the panel
 * - Degraded sources are named instead of the calm empty state
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type {
  CursorPage,
  StandingReplyTemplateDraft,
  StandingReplyTemplatePreview,
} from "@/api/index.ts";

vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...actual,
    listReplyTemplateDrafts: vi.fn(),
    createReplyTemplateDraft: vi.fn(),
    editReplyTemplateDraft: vi.fn(),
    revokeReplyTemplateDraft: vi.fn(),
    previewReplyTemplateDraft: vi.fn(),
    getEntities: vi.fn(),
    getEntity: vi.fn(),
  };
});

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: () => ({
    data: { data: [{ name: "relationship" }, { name: "general" }] },
    isError: false,
    refetch: vi.fn(),
  }),
}));

import {
  ApiError,
  createReplyTemplateDraft,
  editReplyTemplateDraft,
  getEntities,
  getEntity,
  listReplyTemplateDrafts,
  previewReplyTemplateDraft,
  revokeReplyTemplateDraft,
} from "@/api/index.ts";
import { ReplyTemplateDraftsPanel } from "@/components/approvals/reply-template-drafts-panel.tsx";

const ALICE = "11111111-1111-4111-8111-111111111111";
const BOB = "22222222-2222-4222-8222-222222222222";

function draft(overrides: Partial<StandingReplyTemplateDraft> = {}): StandingReplyTemplateDraft {
  return {
    id: "d1",
    butler: "relationship",
    tool_name: "telegram_send_message",
    templates: { text: "Running {mins:duration} late" },
    recipient_entity_ids: [ALICE],
    description: "Late notice",
    state: "draft",
    revision: 3,
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-01T11:00:00Z",
    revoked_at: null,
    ...overrides,
  };
}

function page(
  items: StandingReplyTemplateDraft[],
  extra: Partial<CursorPage<StandingReplyTemplateDraft>> = {},
): CursorPage<StandingReplyTemplateDraft> {
  return { items, next_cursor: null, limit: 50, ...extra };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReplyTemplateDraftsPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(getEntity).mockResolvedValue({
    data: { id: ALICE, canonical_name: "Alice" },
  } as Awaited<ReturnType<typeof getEntity>>);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function openDraft() {
  fireEvent.click(await screen.findByRole("button", { name: /relationship · telegram_send_message/ }));
  return screen.findByTestId("reply-template-draft-inspector");
}

describe("ReplyTemplateDraftsPanel", () => {
  it("authors a draft with pinned recipients and reuses its idempotency key on retry", async () => {
    vi.mocked(listReplyTemplateDrafts).mockResolvedValue(page([]));
    vi.mocked(getEntities).mockResolvedValue({
      data: [{ id: ALICE, canonical_name: "Alice", entity_type: "person" }],
    } as Awaited<ReturnType<typeof getEntities>>);
    vi.mocked(createReplyTemplateDraft)
      .mockRejectedValueOnce(new ApiError("UNKNOWN_ERROR", "slot_type_forbidden", 400, { error: "slot_type_forbidden" }))
      .mockResolvedValueOnce({ data: draft(), meta: {} });
    renderPanel();

    expect(await screen.findByText(/No drafts yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "+ Draft" }));
    const editor = screen.getByTestId("reply-template-draft-editor");
    fireEvent.change(within(editor).getByLabelText("Butler"), { target: { value: "relationship" } });
    fireEvent.change(within(editor).getByLabelText("text template"), {
      target: { value: "Running {mins:duration} late" },
    });
    fireEvent.change(within(editor).getByLabelText(/Recipients/), { target: { value: "ali" } });
    fireEvent.click(await within(editor).findByRole("button", { name: /Alice/ }));
    fireEvent.click(within(editor).getByRole("button", { name: "Save draft" }));

    expect(await within(editor).findByRole("alert")).toHaveProperty(
      "textContent",
      expect.stringMatching(/forbidden type/),
    );
    fireEvent.click(within(editor).getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(createReplyTemplateDraft).toHaveBeenCalledTimes(2));

    const [first, second] = vi.mocked(createReplyTemplateDraft).mock.calls.map((c) => c[0]);
    expect(first).toMatchObject({
      butler: "relationship",
      tool_name: "telegram_send_message",
      templates: { text: "Running {mins:duration} late" },
      recipient_entity_ids: [ALICE],
    });
    expect(first.idempotency_key).toBeTruthy();
    expect(second.idempotency_key).toBe(first.idempotency_key);
    await waitFor(() => expect(screen.queryByTestId("reply-template-draft-editor")).toBeNull());
  });

  it("refuses a forbidden slot type before saving", async () => {
    vi.mocked(listReplyTemplateDrafts).mockResolvedValue(page([]));
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "+ Draft" }));
    const editor = screen.getByTestId("reply-template-draft-editor");
    fireEvent.change(within(editor).getByLabelText("Butler"), { target: { value: "relationship" } });
    fireEvent.change(within(editor).getByLabelText("text template"), {
      target: { value: "Hi {who:name}" },
    });
    expect(within(editor).getByText("(not allowed)")).toBeTruthy();
    fireEvent.click(within(editor).getByRole("button", { name: "Save draft" }));
    expect(within(editor).getByRole("alert").textContent).toMatch(/forbidden type/);
    expect(createReplyTemplateDraft).not.toHaveBeenCalled();
  });

  it("edits against the inspected revision and keeps the edit across a stale revision", async () => {
    vi.mocked(listReplyTemplateDrafts)
      .mockResolvedValueOnce(page([draft()]))
      .mockResolvedValue(page([draft({ revision: 4 })]));
    vi.mocked(editReplyTemplateDraft)
      .mockRejectedValueOnce(
        new ApiError("UNKNOWN_ERROR", "stale_revision", 409, { error: "stale_revision" }),
      )
      .mockResolvedValueOnce({ data: draft({ revision: 5 }), meta: {} });
    renderPanel();
    const inspector = await openDraft();
    fireEvent.click(within(inspector).getByRole("button", { name: "Edit" }));
    const editor = screen.getByTestId("reply-template-draft-editor");
    expect((within(editor).getByLabelText("Butler") as HTMLSelectElement).disabled).toBe(true);
    fireEvent.change(within(editor).getByLabelText("text template"), {
      target: { value: "Running {mins:duration} behind" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "Save revision" }));

    expect((await within(editor).findByRole("alert")).textContent).toMatch(/changed since you opened it/);
    const edit = {
      templates: { text: "Running {mins:duration} behind" },
      recipient_entity_ids: [ALICE],
      description: "Late notice",
    };
    expect(editReplyTemplateDraft).toHaveBeenLastCalledWith("d1", { expected_revision: 3, ...edit });

    // The newer revision reloads underneath without wiping the unsaved edit.
    await screen.findByText(/edit draft · revision 4/i);
    const reloaded = screen.getByTestId("reply-template-draft-editor");
    expect((within(reloaded).getByLabelText("text template") as HTMLTextAreaElement).value).toBe(
      edit.templates.text,
    );
    fireEvent.click(within(reloaded).getByRole("button", { name: "Save revision" }));
    await waitFor(() =>
      expect(editReplyTemplateDraft).toHaveBeenLastCalledWith("d1", { expected_revision: 4, ...edit }),
    );
  });

  it("revokes only after an explicit confirm, sending the current revision", async () => {
    vi.mocked(listReplyTemplateDrafts).mockResolvedValue(page([draft()]));
    vi.mocked(revokeReplyTemplateDraft).mockResolvedValue({ data: draft({ state: "revoked" }), meta: {} });
    renderPanel();
    const inspector = await openDraft();
    fireEvent.click(within(inspector).getByRole("button", { name: "Revoke" }));
    expect(revokeReplyTemplateDraft).not.toHaveBeenCalled();
    fireEvent.click(within(inspector).getByRole("button", { name: "Confirm revoke" }));
    await waitFor(() => expect(revokeReplyTemplateDraft).toHaveBeenCalledWith("d1", 3));
  });

  it("keeps a failed revoke's error on screen with revoke-specific copy", async () => {
    vi.mocked(listReplyTemplateDrafts).mockResolvedValue(page([draft()]));
    vi.mocked(revokeReplyTemplateDraft).mockRejectedValue(
      new ApiError("UNKNOWN_ERROR", "stale_revision", 409, { error: "stale_revision" }),
    );
    renderPanel();
    const inspector = await openDraft();
    fireEvent.click(within(inspector).getByRole("button", { name: "Revoke" }));
    fireEvent.click(within(inspector).getByRole("button", { name: "Confirm revoke" }));

    const alert = await within(inspector).findByRole("alert");
    expect(alert.textContent).toMatch(/confirm again to revoke/);
    expect(alert.textContent).not.toMatch(/Save again/);
    expect(within(inspector).getByRole("button", { name: "Confirm revoke" })).toBeTruthy();
  });

  it("moves focus into the opened editor and back to its trigger on close", async () => {
    vi.mocked(listReplyTemplateDrafts).mockResolvedValue(page([draft()]));
    vi.mocked(editReplyTemplateDraft).mockResolvedValue({ data: draft({ revision: 4 }), meta: {} });
    renderPanel();

    const newDraft = await screen.findByRole("button", { name: "+ Draft" });
    fireEvent.click(newDraft);
    expect(document.activeElement?.textContent).toBe("New standing-reply draft");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(document.activeElement).toBe(newDraft));

    const inspector = await openDraft();
    const edit = within(inspector).getByRole("button", { name: "Edit" });
    fireEvent.click(edit);
    expect(document.activeElement?.textContent).toMatch(/Edit draft/);
    fireEvent.click(screen.getByRole("button", { name: "Save revision" }));
    await waitFor(() => expect(document.activeElement).toBe(edit));
  });

  it("labels unpin controls by name, never by raw id", async () => {
    vi.mocked(listReplyTemplateDrafts).mockResolvedValue(
      page([draft({ recipient_entity_ids: [ALICE, BOB] })]),
    );
    // Alice resolves; Bob's lookup never answers.
    vi.mocked(getEntity).mockImplementation((id) =>
      id === ALICE
        ? Promise.resolve({ data: { id: ALICE, canonical_name: "Alice" } } as Awaited<
            ReturnType<typeof getEntity>
          >)
        : new Promise(() => {}),
    );
    renderPanel();
    const inspector = await openDraft();
    fireEvent.click(within(inspector).getByRole("button", { name: "Edit" }));
    const editor = screen.getByTestId("reply-template-draft-editor");

    expect(await within(editor).findByRole("button", { name: "Unpin Alice" })).toBeTruthy();
    expect(within(editor).getByRole("button", { name: "Unpin recipient 2" })).toBeTruthy();
    for (const button of within(editor).getAllByRole("button", { name: /^Unpin/ })) {
      expect(button.getAttribute("aria-label")).not.toMatch(/[0-9a-f]{8}-/);
    }
  });

  it("previews a call with authority none and humanized reasons", async () => {
    vi.mocked(listReplyTemplateDrafts).mockResolvedValue(page([draft()]));
    const preview: StandingReplyTemplatePreview = {
      draft_id: "d1",
      revision: 3,
      matched: true,
      eligible: false,
      reasons: ["recipient_not_pinned"],
      slots: {},
      authority: "none",
    };
    vi.mocked(previewReplyTemplateDraft).mockResolvedValue({ data: preview, meta: {} });
    renderPanel();
    const inspector = await openDraft();
    expect(await within(inspector).findByText("Alice")).toBeTruthy();
    fireEvent.change(within(inspector).getByLabelText("chat_id"), { target: { value: "42" } });
    fireEvent.change(within(inspector).getByLabelText("text"), {
      target: { value: "Running 10 minutes late" },
    });
    fireEvent.click(within(inspector).getByRole("button", { name: "Run preview" }));

    const result = await within(inspector).findByTestId("reply-template-preview-result");
    expect(previewReplyTemplateDraft).toHaveBeenCalledWith("d1", {
      tool_name: "telegram_send_message",
      tool_args: { chat_id: "42", text: "Running 10 minutes late" },
    });
    expect(result.textContent).toMatch(/would not match/);
    expect(result.textContent).toMatch(/not one of the pinned recipients/);
    expect(within(result).getByTestId("authority-none").textContent).toBe("authority: none");
  });

  it("offers no activation control and hides edit/revoke on revoked drafts", async () => {
    vi.mocked(listReplyTemplateDrafts).mockResolvedValue(
      page([draft({ state: "revoked", revoked_at: "2026-11-01T12:00:00Z" })]),
    );
    renderPanel();
    const inspector = await openDraft();
    const panel = screen.getByTestId("reply-template-drafts-panel");
    expect(within(panel).queryByRole("button", { name: /activat|enable|promote/i })).toBeNull();
    expect(within(inspector).queryByRole("button", { name: "Edit" })).toBeNull();
    expect(within(inspector).queryByRole("button", { name: "Revoke" })).toBeNull();
  });

  it("names degraded sources instead of the calm empty state", async () => {
    vi.mocked(listReplyTemplateDrafts).mockResolvedValue(page([], { sources_degraded: ["general"] }));
    renderPanel();
    expect((await screen.findByRole("alert")).textContent).toMatch(/general unavailable/);
    expect(screen.queryByText(/No drafts yet/)).toBeNull();
  });
});
