import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Time } from "@/components/ui/time";
import { X } from "lucide-react";
import { useMindMap } from "@/hooks/use-education";
import { useModalChoreography } from "@/hooks/use-modal-choreography";
import { masteryStatusBadgeClassName } from "./mastery-status";
import QuizHistoryList from "./QuizHistoryList";

interface NodeDetailPanelProps {
  mindMapId: string | null;
  nodeId: string | null;
  onClose: () => void;
}

export default function NodeDetailPanel({
  mindMapId,
  nodeId,
  onClose,
}: NodeDetailPanelProps) {
  const { data: mindMap } = useMindMap(mindMapId);
  const node = mindMap?.nodes?.find((n) => n.id === nodeId);

  // Focus choreography (bu-x7syp): on open, focus moves into the panel and
  // an accessible heading announces which node it's showing; on close,
  // focus returns to the triggering control (ReviewEntryRow /
  // MindMapGraph.handleNodeClick / StrugglingNodesCard row). Uses the shared
  // useModalChoreography hook, matching the TimelineEventDrawer/EventDrawer
  // inline-disclosure-panel convention. `trapFocus: false` since this panel
  // renders inline below page content (no scrim), so Tab should stay free
  // to leave it. `active` is tied to node readiness rather than the hook's
  // mount-based default: this component can render `null` internally (its
  // own `useMindMap` still loading) while the parent keeps it mounted, so
  // the focus-in effect must be able to fire once `node` actually becomes
  // available, not just on the component's own mount.
  const { rootRef, initialFocusRef, onKeyDown } = useModalChoreography<HTMLHeadingElement>({
    onClose,
    trapFocus: false,
    active: !!node,
  });

  if (!nodeId || !node) {
    return null;
  }

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- onKeyDown here is Escape-to-close only (trapFocus: false — no Tab handling), matching the accepted TimelineEventDrawer/EventDrawer inline-disclosure-panel pattern.
    <div ref={rootRef} role="complementary" aria-label="Node detail panel" onKeyDown={onKeyDown}>
      {/* Focus lands here on open (tabIndex=-1: programmatically focusable,
          not a Tab stop); visually hidden since CardTitle below already
          carries the same information for sighted users. */}
      <h2 ref={initialFocusRef} tabIndex={-1} className="sr-only focus:outline-none">
        Node details: {node.label}
      </h2>
      <Card>
        <CardHeader className="flex flex-row items-start justify-between space-y-0">
          <div className="space-y-1">
            <CardTitle className="text-lg">{node.label}</CardTitle>
            <Badge className={masteryStatusBadgeClassName(node.mastery_status)}>
              {node.mastery_status}
            </Badge>
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close node details"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          {node.description && (
            <p className="text-sm text-muted-foreground">{node.description}</p>
          )}

          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <span className="text-muted-foreground">Mastery</span>
              <p className="font-medium">{Math.round(node.mastery_score * 100)}%</p>
            </div>
            <div>
              <span className="text-muted-foreground">Ease Factor</span>
              <p className="font-medium">{node.ease_factor.toFixed(2)}</p>
            </div>
            <div>
              <span className="text-muted-foreground">Repetitions</span>
              <p className="font-medium">{node.repetitions}</p>
            </div>
            {node.effort_minutes != null && (
              <div>
                <span className="text-muted-foreground">Effort</span>
                <p className="font-medium">{node.effort_minutes} min</p>
              </div>
            )}
            {node.next_review_at && (
              <div className="col-span-2">
                <span className="text-muted-foreground">Next Review</span>
                <p className="font-medium">
                  <Time value={node.next_review_at} mode="absolute" precision="day" />
                </p>
              </div>
            )}
          </div>

          <div className="border-t pt-4">
            <h4 className="mb-2 text-sm font-medium">Quiz History</h4>
            <QuizHistoryList mindMapId={mindMapId!} nodeId={nodeId} compact />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
