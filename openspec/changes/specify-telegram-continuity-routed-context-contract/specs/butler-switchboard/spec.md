# Butler Switchboard — Delta

## REMOVED Requirements

### Requirement: Misroute Correction Re-dispatch

**Reason**: RFC 0038 D1 removes classification and route dispatch with no successor.
Canonical `bu-u5euwb.16` and generalist's collision disposition retire sticky target
affinity, TTL reclassification and misroute re-dispatch. There is no `correct_route`
re-dispatch or Telegram correction re-pin in the goal runtime. This removes the
whole prior tool block, including all six baseline scenarios and its additional
Telegram-affinity scenario; it does not silently modify a surviving tool.

**Migration**: None for re-dispatch. For a wrong goal outcome the owner starts a new
goal or Stops the current one (`REQ-capability-authorization-011`); a lead that chose
the wrong domain calls the right capability within its existing grant and goal
(`REQ-goal-orchestration-001`). The original accepted ingest/request reference and
server-held sender context remain on goal admission. No replacement domain session,
correction ID, copied route envelope or target-affinity record is created.
