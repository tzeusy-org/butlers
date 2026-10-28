import { expect } from "@playwright/test";
import { conceptLabel, registeredUrl, test } from "./fixtures/education-source-annotations";

// Requirement: REQ-education-source-grounding-002
// Browser annotation-display evidence only; real API/source reading is not exercised.
test.describe("Education source annotation browser evidence", () => {
  test("opens the actual node panel and distinguishes citations from weaker provenance", async ({ page }) => {
    await page.goto("/education");
    await expect(page.getByRole("heading", { name: "Education", exact: true })).toBeVisible();
    await page.getByText(conceptLabel, { exact: true }).click();
    const panel = page.getByRole("complementary", { name: "Node detail panel" });
    await expect(panel.getByRole("heading", { name: "Sources", exact: true })).toBeVisible();
    const row = (location: string) => panel.getByRole("listitem").filter({ has: page.getByText(location, { exact: true }) });

    const linked = row("linked chapter");
    await expect(linked.getByText("Referenced", { exact: true })).toBeVisible();
    await expect(linked.getByText("Synthetic linked notes", { exact: true })).toBeVisible();
    await expect(linked.getByText("Synthetic verified-location note")).toBeVisible();
    await expect(linked.getByRole("link", { name: "Open registered source" })).toHaveAttribute("href", registeredUrl);

    const offline = row("offline chapter");
    await expect(offline.getByText("Referenced", { exact: true })).toBeVisible();
    await expect(offline.getByText("Synthetic offline workbook")).toBeVisible();
    await expect(offline.getByRole("link")).toHaveCount(0);

    for (const location of ["recalled chapter", "unsourced recollection", "unknown provenance chapter"]) {
      const recalled = row(location);
      await expect(recalled.getByText("Model-recalled", { exact: true })).toBeVisible();
      await expect(recalled.getByText(/did not read the source.*unverified/)).toBeVisible();
      await expect(recalled.getByText("Referenced", { exact: true })).toHaveCount(0);
      await expect(recalled.getByRole("link")).toHaveCount(0);
    }
    await expect(row("unsourced recollection").getByText("No registered source named")).toBeVisible();

    const dangling = row("removed chapter");
    await expect(dangling.getByText("Source no longer registered", { exact: true })).toBeVisible();
    await expect(dangling.getByText("Reference ID: synthetic-removed")).toBeVisible();
    await expect(dangling.getByText(/is not a citation/)).toBeVisible();
    await expect(dangling.getByRole("link")).toHaveCount(0);
    await expect(panel.getByRole("link", { name: "Open registered source" })).toHaveCount(1);
  });

  test.describe("when the registry cannot be checked", () => {
    test.use({ registryAvailable: false });
    test("names the unavailable lookup instead of calling sources dangling or referenced", async ({ page }) => {
      await page.goto("/education");
      await page.getByText(conceptLabel, { exact: true }).click();
      const panel = page.getByRole("complementary", { name: "Node detail panel" });
      await expect(panel.getByText("Not checked against the registry", { exact: true })).toHaveCount(5);
      await expect(panel.getByText(/could not be reached/)).toHaveCount(5);
      await expect(panel.getByText("Source no longer registered", { exact: true })).toHaveCount(0);
      await expect(panel.getByText("Referenced", { exact: true })).toHaveCount(0);
      await expect(panel.getByText("Synthetic linked notes", { exact: true })).toHaveCount(0);
      await expect(panel.getByRole("link")).toHaveCount(0);
      await expect(panel.getByText("Model-recalled", { exact: true })).toHaveCount(1);
    });
  });
});
