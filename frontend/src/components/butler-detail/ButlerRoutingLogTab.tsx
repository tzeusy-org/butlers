/**
 * ButlerRoutingLogTab — Routing Log tab for the Switchboard butler detail page.
 *
 * Wraps the RoutingLogTable component in a Panel atom (bu-pllml).
 */

import RoutingLogTable from "@/components/switchboard/RoutingLogTable.tsx";
import { ButlerPanelGrid, Panel } from "@/components/butler-detail/atoms";

export default function ButlerRoutingLogTab() {
  return (
    <ButlerPanelGrid data-testid="butler-routing-log-tab">
      <Panel title="routing log" span={4} scroll={true} height="480px" testId="routing-log-panel">
        <RoutingLogTable />
      </Panel>
    </ButlerPanelGrid>
  );
}
