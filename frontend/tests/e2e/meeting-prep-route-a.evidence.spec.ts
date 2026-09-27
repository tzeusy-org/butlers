import { expect, test } from "@playwright/test";

import { writeSanitizedApiReceipt } from "../../route-a-api-receipt.mjs";

const populatedTitle = process.env.ROUTE_A_POPULATED_EVENT_TITLE ?? "";
const emptyTitle = process.env.ROUTE_A_EMPTY_EVENT_TITLE ?? "";
const populatedEventId = process.env.ROUTE_A_POPULATED_EVENT_ID ?? "";
const emptyEventId = process.env.ROUTE_A_EMPTY_EVENT_ID ?? "";
const artifactDir = process.env.ROUTE_A_ARTIFACT_DIR ?? "";

if (
  process.env.ROUTE_A_EVIDENCE !== "1" ||
  !populatedTitle ||
  !emptyTitle ||
  !populatedEventId ||
  !emptyEventId ||
  !artifactDir
) {
  throw new Error("Route A evidence requires the isolated browser service configuration.");
}

test.describe("Route A meeting-prep browser evidence", () => {
  test("renders a populated synthetic cached-view contribution without request interception", async ({ page }) => {
    const prepResponse = page.waitForResponse((response) =>
      response.url().includes("/api/calendar/workspace/prep/") && response.status() === 200,
    );

    await page.goto("/calendar");
    await page.getByText(populatedTitle).first().click();
    const response = await prepResponse;
    const payload = await response.json();

    expect(payload.data.has_prep_context).toBe(true);
    const receipt = await writeSanitizedApiReceipt({
      artifactDir,
      payload,
      expected: {
        fixture: "populated",
        eventId: populatedEventId,
        hasPrepContext: true,
      },
    });
    expect(receipt.commitments).toHaveLength(2);
    const rail = page.getByRole("region", { name: "Meeting prep" });
    await expect(rail).toBeVisible();
    const commitments = rail.getByRole("list", { name: /Commitments for Route A Synthetic Attendee/ });
    await expect(commitments).toBeVisible();
    await expect(commitments.getByText("Owner owes")).toBeVisible();
    await expect(commitments.getByText("Counterparty owes owner")).toBeVisible();
    await expect(rail.locator('[data-escalation-level="L3"][data-escalated="true"]')).toBeVisible();
    await expect(commitments.getByRole("listitem").first()).toHaveAttribute("aria-label", /Owner owes/);
  });

  test("renders the honest empty state without a fabricated commitment list", async ({ page }) => {
    const prepResponse = page.waitForResponse((response) =>
      response.url().includes("/api/calendar/workspace/prep/") && response.status() === 200,
    );

    await page.goto("/calendar");
    await page.getByText(emptyTitle).first().click();
    const response = await prepResponse;
    const payload = await response.json();

    expect(payload.data.has_prep_context).toBe(false);
    const receipt = await writeSanitizedApiReceipt({
      artifactDir,
      payload,
      expected: {
        fixture: "empty",
        eventId: emptyEventId,
        hasPrepContext: false,
      },
    });
    expect(receipt.commitments).toEqual([]);
    const rail = page.getByRole("region", { name: "Meeting prep" });
    await expect(rail.getByText("No prep context yet")).toBeVisible();
    await expect(rail.getByRole("list")).toHaveCount(0);
  });
});
