// @vitest-environment jsdom

import { lazy, type ComponentType } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { RouteSuspense } from "./RouteSuspense";

describe("RouteSuspense", () => {
  it("shows the shared route skeleton until a lazy route module resolves", async () => {
    let resolveModule!: (module: { default: ComponentType }) => void;
    const DeferredRoute = lazy(
      () =>
        new Promise<{ default: ComponentType }>((resolve) => {
          resolveModule = resolve;
        }),
    );

    render(
      <RouteSuspense>
        <DeferredRoute />
      </RouteSuspense>,
    );

    expect(
      screen.getByRole("status", { name: "Loading page" }).getAttribute("data-testid"),
    ).toBe("route-suspense-skeleton");

    resolveModule({ default: () => <div data-testid="loaded-route">Loaded route</div> });

    expect((await screen.findByTestId("loaded-route")).textContent).toBe("Loaded route");
    expect(screen.queryByTestId("route-suspense-skeleton")).toBeNull();
  });
});
