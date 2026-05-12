/**
 * ButlerRegistryTab — Registry tab for the Switchboard butler detail page.
 *
 * Wraps the RegistryTable component in a Panel atom (bu-b9jpn).
 */

import RegistryTable from "@/components/switchboard/RegistryTable.tsx";
import { Panel } from "@/components/butler-detail/atoms";

export default function ButlerRegistryTab() {
  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-4 border-t border-l border-border/60"
      data-testid="butler-registry-tab"
    >
      <Panel title="butler registry" span={4} testId="butler-registry-panel">
        <RegistryTable />
      </Panel>
    </div>
  );
}
