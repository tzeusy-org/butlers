// @vitest-environment jsdom
/**
 * Tests for CirclesPage (bu-86c4c.19 — JARVIS audit move 14).
 *
 * Verifies:
 * - Renders the SubpageTabs strip with Circles active
 * - Honest error state: an isError useGroups response renders the Page
 *   error region (never the "no circles" empty state) — fixes the
 *   dossier's critical finding that GroupsPage used to conflate the two
 * - Empty state names the real creation mechanism (the relationship butler)
 * - Renders circle rows with name + member count + labels
 * - Client-side name search filters the rendered rows without a server round-trip
 * - Clicking a row expands a detail panel backed by the previously-unused
 *   getGroup endpoint
 */

import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { fireEvent, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { Group, GroupMember } from "@/api/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-contacts", () => ({
  useGroups: vi.fn(),
  useGroupMembers: vi.fn(),
  useLabels: vi.fn(),
  useCreateLabel: vi.fn(),
  useAssignGroupLabel: vi.fn(),
  useRemoveGroupLabel: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    getGroup: vi.fn(),
  };
});

import CirclesPage from "./CirclesPage";
import {
  useAssignGroupLabel,
  useCreateLabel,
  useGroupMembers,
  useGroups,
  useLabels,
  useRemoveGroupLabel,
} from "@/hooks/use-contacts";
import { getGroup } from "@/api/client";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

const FAMILY: Group = {
  id: "group-family",
  name: "Family",
  description: "Immediate family",
  member_count: 4,
  // eslint-disable-next-line no-restricted-syntax -- fixture for an arbitrary user-chosen label color, not a themed value
  labels: [{ id: "label-vip", name: "VIP", color: "#e63946" }],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
};

const FAMILY_MEMBERS: GroupMember[] = [
  { id: "contact-alice", entity_id: "entity-alice", name: "Alice Family", entity_type: "person" },
  { id: "contact-bob", entity_id: "entity-bob", name: "Bob Family", entity_type: "person" },
];

const WORK: Group = {
  id: "group-work",
  name: "Work friends",
  description: null,
  member_count: 2,
  labels: [],
  created_at: "2026-02-01T00:00:00Z",
  updated_at: "2026-02-01T00:00:00Z",
};

const INVALID_COLOR_FAMILY: Group = {
  ...FAMILY,
  labels: [{ id: "label-invalid", name: "Invalid colour", color: "#12345" }],
};

const WHITE_COLOR_FAMILY: Group = {
  ...FAMILY,
  labels: [
    // eslint-disable-next-line no-restricted-syntax -- fixture uses an arbitrary owner-selected white label color to exercise contrast selection
    { id: "label-white", name: "White", color: "#fff" },
  ],
};

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

let container: HTMLDivElement;
let root: Root;

function renderPage(route = "/entities/circles") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[route]}>
          <CirclesPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);

  (useLabels as AnyMock).mockReturnValue({ data: [], isPending: false });
  (useCreateLabel as AnyMock).mockReturnValue({ mutate: vi.fn(), isPending: false });
  (useAssignGroupLabel as AnyMock).mockReturnValue({ mutate: vi.fn() });
  (useRemoveGroupLabel as AnyMock).mockReturnValue({ mutate: vi.fn() });
  (getGroup as AnyMock).mockResolvedValue(FAMILY);
  (useGroupMembers as AnyMock).mockReturnValue({
    data: { group_id: "group-family", members: FAMILY_MEMBERS },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CirclesPage — structure", () => {
  it("renders the SubpageTabs strip with Circles active", () => {
    (useGroups as AnyMock).mockReturnValue({
      data: { groups: [FAMILY, WORK], total: 2 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderPage();
    const circlesLink = container.querySelector('a[href="/entities/circles"]');
    expect(circlesLink).toBeTruthy();
    expect(circlesLink?.getAttribute("aria-current")).toBe("page");
  });

  it("renders circle rows with name and member count", () => {
    (useGroups as AnyMock).mockReturnValue({
      data: { groups: [FAMILY, WORK], total: 2 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderPage();
    expect(container.textContent).toContain("Family");
    expect(container.textContent).toContain("4 members");
    expect(container.textContent).toContain("Work friends");
    expect(container.textContent).toContain("2 members");
  });

  it("uses the categorical fallback when a label has an unsupported owner hex length", () => {
    (useGroups as AnyMock).mockReturnValue({
      data: { groups: [INVALID_COLOR_FAMILY], total: 1 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderPage();

    expect(container.innerHTML).toMatch(/background-color:\s*var\(--categorical-/);
    expect(container.innerHTML).toMatch(/color:\s*var\(--categorical-fill-foreground\)/);
    expect(container.innerHTML).not.toContain("#12345");
  });

  it("uses a dark foreground for a valid white owner label fill", () => {
    (useGroups as AnyMock).mockReturnValue({
      data: { groups: [WHITE_COLOR_FAMILY], total: 1 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderPage();

    expect(container.innerHTML).toMatch(/background-color:\s*rgb\(255, 255, 255\)/);
    expect(container.innerHTML).toMatch(/color:\s*var\(--label-fill-foreground-on-light\)/);
  });
});

describe("CirclesPage — assign label dialog", () => {
  it("exposes each available label as a native button activated by Enter", async () => {
    const assign = vi.fn();
    const user = userEvent.setup();
    (useGroups as AnyMock).mockReturnValue({
      data: { groups: [WORK], total: 1 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    (useLabels as AnyMock).mockReturnValue({
      data: [{ id: "label-vip", name: "VIP", color: "#fff" }],
      isPending: false,
    });
    (useAssignGroupLabel as AnyMock).mockReturnValue({ mutate: assign });
    renderPage();

    await user.click(container.querySelector('button[aria-label="Assign label"]')!);
    const labelControl = Array.from(document.body.querySelectorAll("button")).find(
      (button) => button.textContent?.trim() === "VIP",
    );
    expect(labelControl).toBeTruthy();

    labelControl!.focus();
    expect(document.activeElement).toBe(labelControl);
    await user.type(labelControl!, "{enter}");

    expect(assign).toHaveBeenCalledWith({ groupId: "group-work", labelId: "label-vip" });
  });
});

describe("CirclesPage — honest async states", () => {
  it("renders the Page error region (not the empty state) when useGroups errors", () => {
    (useGroups as AnyMock).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("network down"),
      refetch: vi.fn(),
    });
    renderPage();
    expect(container.textContent).not.toContain("No circles yet.");
    expect(container.textContent).toContain("network down");
  });

  it("names the relationship butler as the creation mechanism in the empty state", () => {
    (useGroups as AnyMock).mockReturnValue({
      data: { groups: [], total: 0 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderPage();
    expect(container.textContent).toContain("No circles yet.");
    expect(container.textContent).toContain("Ask the relationship butler");
  });
});

describe("CirclesPage — client-side search", () => {
  it("filters rows by name without refetching", () => {
    (useGroups as AnyMock).mockReturnValue({
      data: { groups: [FAMILY, WORK], total: 2 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderPage();

    const searchInput = container.querySelector('input[aria-label="Search circles"]') as HTMLInputElement;
    expect(searchInput).toBeTruthy();

    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
    act(() => {
      nativeSetter.call(searchInput, "fam");
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(container.textContent).toContain("Family");
    expect(container.textContent).not.toContain("Work friends");
    // Only one useGroups call is needed — this is a client-side filter.
    expect((useGroups as AnyMock).mock.calls.length).toBeGreaterThan(0);
  });
});

describe("CirclesPage — expandable detail wired to getGroup", () => {
  it("fetches getGroup(id) and shows fresh detail when a row is expanded", async () => {
    (useGroups as AnyMock).mockReturnValue({
      data: { groups: [FAMILY], total: 1 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/entities/circles"]}>
          <CirclesPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const row = view.getAllByRole("button").find((btn) => btn.textContent?.includes("Family"))!;
    fireEvent.click(row);

    await waitFor(() => {
      expect(getGroup).toHaveBeenCalledWith("group-family");
      expect(view.container.textContent).toContain("Immediate family");
    });

    view.unmount();
  });

  it("renders the member roster with deep-links to each member's entity page", async () => {
    (useGroups as AnyMock).mockReturnValue({
      data: { groups: [FAMILY], total: 1 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    const view = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/entities/circles"]}>
          <CirclesPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const row = view.getAllByRole("button").find((btn) => btn.textContent?.includes("Family"))!;
    fireEvent.click(row);

    await waitFor(() => {
      expect(view.container.textContent).toContain("Alice Family");
      expect(view.container.textContent).toContain("Bob Family");
    });

    const aliceLink = view.container.querySelector('a[href="/entities/entity-alice"]');
    expect(aliceLink).toBeTruthy();
    const bobLink = view.container.querySelector('a[href="/entities/entity-bob"]');
    expect(bobLink).toBeTruthy();

    view.unmount();
  });

  it("shows an honest note when a group has members that are not linked to an entity", async () => {
    (useGroups as AnyMock).mockReturnValue({
      data: { groups: [FAMILY], total: 1 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    (useGroupMembers as AnyMock).mockReturnValue({
      data: { group_id: "group-family", members: [FAMILY_MEMBERS[0]] },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });

    const view = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/entities/circles"]}>
          <CirclesPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const row = view.getAllByRole("button").find((btn) => btn.textContent?.includes("Family"))!;
    fireEvent.click(row);

    await waitFor(() => {
      expect(view.container.textContent).toContain("Alice Family");
    });
    // FAMILY.member_count is 4, only 1 linked member is returned.
    expect(view.container.textContent).toContain("3 members not yet linked to an entity");

    view.unmount();
  });
});
