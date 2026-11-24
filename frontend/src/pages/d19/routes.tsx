import { lazy } from "react";
import type { RouteObject } from "react-router";
import { D19_PREVIEW_ENABLED, D19_PREFIX } from "@/lib/d19";
import { RouteSuspense } from "@/components/layout/RouteSuspense";
import { D19Layout } from "./shared";
import { getShellCapability } from "@/lib/shell-capability";

function registeredPage(path: string) {
  const capability = getShellCapability(path);
  if (!capability) throw new Error(`Missing D19 shell capability for ${path}`);
  return lazy(capability.loader);
}

function previewRoutes(): RouteObject[] {
  const Overview = registeredPage(D19_PREFIX);
  const Goals = registeredPage(`${D19_PREFIX}/goals`);
  const Goal = registeredPage(`${D19_PREFIX}/goals/:goalId`);
  const Regions = registeredPage(`${D19_PREFIX}/regions`);
  const Region = registeredPage(`${D19_PREFIX}/regions/:region`);
  const Approvals = registeredPage(`${D19_PREFIX}/approvals`);
  return [{
  path: D19_PREFIX, element: <D19Layout />, children: [
    { index: true, element: <RouteSuspense><Overview /></RouteSuspense> },
    { path: "goals", element: <RouteSuspense><Goals /></RouteSuspense> },
    { path: "goals/:goalId", element: <RouteSuspense><Goal /></RouteSuspense> },
    { path: "regions", element: <RouteSuspense><Regions /></RouteSuspense> },
    { path: "regions/:region", element: <RouteSuspense><Region /></RouteSuspense> },
    { path: "approvals", element: <RouteSuspense><Approvals /></RouteSuspense> },
    { path: "approvals/:id", element: <RouteSuspense><Approvals /></RouteSuspense> },
  ],
  }];
}

/** Unregistered when off, including direct URLs. Never changes the current root route. */
export const d19Routes: RouteObject[] = D19_PREVIEW_ENABLED ? previewRoutes() : [];
