// @vitest-environment jsdom
/**
 * Spine duplicate-key regression (bu-ffjig).
 *
 * Two user identities can hold a credential on the SAME provider (owner-default
 * projection includes every identity's creds — MOCK_INVENTORY has google/tze and
 * google/wei). The spine `key` is `u:<provider>` (a provider-level focus
 * deep-link target, deliberately NOT identity-qualified), so those two rows
 * share `key` and React's real-DOM reconciler warned "two children with the same
 * key". The fix qualifies the React reconciliation key with the identity while
 * leaving `entry.key` (focus/selection) provider-level.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

import { Spine } from "./Spine.tsx";
import { buildSpineEntries } from "./spine-builder.ts";
import { MOCK_INVENTORY } from "./mock-data.ts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderSpine() {
  // Owner-default: pass every identity so both google creds (tze + wei) land in
  // the spine — this is the collision scenario.
  const identityIds = MOCK_INVENTORY.identities.map((i) => i.id);
  const entries = buildSpineEntries(MOCK_INVENTORY, identityIds);
  const providers = Object.fromEntries(
    Object.entries(MOCK_INVENTORY.providers).map(([slug, p]) => [
      slug,
      { glyph: p.glyph, label: p.label },
    ]),
  );
  const result = render(
    <Spine
      entries={entries}
      activeKey=""
      onSelect={() => {}}
      onSortChange={() => {}}
      search=""
      onSearchChange={() => {}}
      identities={MOCK_INVENTORY.identities}
      activeIdentityId={identityIds[0]}
      onIdentityChange={() => {}}
      providers={providers}
    />,
  );
  return { entries, ...result };
}

describe("Spine duplicate-key (bu-ffjig)", () => {
  it("renders two same-provider identities without a duplicate-key warning", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { entries } = renderSpine();

    // Sanity: the collision scenario is actually present (two u:google entries).
    const googleEntries = entries.filter((e) => e.key === "u:google");
    expect(googleEntries.length).toBe(2);
    // Their identities differ, which is what disambiguates the React key.
    expect(new Set(googleEntries.map((e) => e.identity)).size).toBe(2);

    // No React "same key" / "duplicate key" warning was emitted on render.
    const dupeWarning = errorSpy.mock.calls.some((call) =>
      call.some(
        (arg) =>
          typeof arg === "string" &&
          /same key|two children with the same key|duplicate key/i.test(arg),
      ),
    );
    expect(dupeWarning).toBe(false);
  });

  it("keeps entry.key provider-level so focus deep-links still resolve", () => {
    const { entries } = renderSpine();
    // The focus/selection key must remain `u:<provider>` (identity lives only on
    // the React reconciliation key) so `?focus=u:google` continues to match.
    const google = entries.filter((e) => e.key === "u:google");
    expect(google.length).toBe(2);
    for (const e of google) expect(e.key).toBe("u:google");
  });
});
