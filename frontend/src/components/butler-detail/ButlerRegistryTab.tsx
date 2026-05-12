/**
 * ButlerRegistryTab — Registry tab for the Switchboard butler detail page.
 *
 * Wraps the RegistryTable component in a Panel atom (bu-b9jpn).
 */

import RegistryTable from "@/components/switchboard/RegistryTable.tsx";
import { ButlerPanelGrid, Panel } from "@/components/butler-detail/atoms";

export default function ButlerRegistryTab() {
  return (
    <ButlerPanelGrid data-testid="butler-registry-tab">
      <Panel title="butler registry" span={4} testId="butler-registry-panel">
        <RegistryTable />
      </Panel>
    </ButlerPanelGrid>
  );
}
