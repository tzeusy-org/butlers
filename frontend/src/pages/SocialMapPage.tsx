/**
 * SocialMapPage — thin wrapper for the Dunbar social map at /entities/social-map.
 *
 * Renders the Page archetype shell + SubpageTabs chrome + SocialMapView body.
 * All interactive state and canvas logic live in SocialMapView, keeping this
 * file aligned with the §8.x SubpageTabs pattern (EntitiesIndexPage, HopPage,
 * ColumnsPage, ConcentrationPage).
 *
 * Spec: openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/tasks.md §8.5
 */

import { Page } from "@/components/ui/page";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import { SocialMapView } from "@/components/relationship/SocialMapView";

export default function SocialMapPage() {
  return (
    <Page
      archetype="overview"
      title="Social map"
      description="Contacts arranged by Dunbar tier, from inner circle (5) to acquaintances (1500)."
      breadcrumbs={[{ label: "Entities", href: "/entities" }, { label: "Social map" }]}
    >
      {/* SubpageTabs — Social map is active */}
      <SubpageTabs />

      <SocialMapView />
    </Page>
  );
}
