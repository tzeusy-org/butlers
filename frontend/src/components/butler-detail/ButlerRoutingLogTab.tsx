/**
 * ButlerRoutingLogTab — Routing Log tab for the Switchboard butler detail page.
 *
 * Wraps the RoutingLogTable component in a Panel atom (bu-pllml).
 */

import RoutingLogTable from "@/components/switchboard/RoutingLogTable.tsx";
import { Panel } from "@/components/butler-detail/atoms";

export default function ButlerRoutingLogTab() {
  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-4 border-t border-l border-border/60"
      data-testid="butler-routing-log-tab"
    >
      <Panel title="routing log" span={4} scroll={true} height="480px" testId="routing-log-panel">
        <RoutingLogTable />
      </Panel>
    </div>
  );
}
