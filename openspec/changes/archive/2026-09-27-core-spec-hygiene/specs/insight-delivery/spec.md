## MODIFIED Requirements

### Requirement: Engagement Tracking
The delivery pipeline SHALL track whether the user engaged with delivered insights to feed the adaptive delivery mechanism.

#### Scenario: Engagement row creation
- **WHEN** an insight (standalone or digest) is delivered
- **THEN** one engagement tracking row SHALL be created per delivered candidate in `public.insight_engagement` with `insight_id`, `delivered_at`, and `engaged=FALSE`

#### Scenario: Engagement detection window
- **WHEN** the OWNER sends any message to any butler (via any ingress channel) within 60 minutes of the insight's `delivered_at`
- **THEN** all engagement rows with `delivered_at` within the preceding 60 minutes and `engaged=FALSE` SHALL be updated to `engaged=TRUE`

#### Scenario: Engagement detection mechanism
- **WHEN** the Switchboard processes an ingress request
- **THEN** it SHALL resolve the sender's identity via the standard channel reverse-lookup (`resolve_contact_by_channel`) and, ONLY when the sender resolves to the owner, check `public.insight_engagement` for rows with `engaged=FALSE` and `delivered_at` within the last 60 minutes
- **AND** if any exist, it SHALL update them to `engaged=TRUE`
- **AND** ingress from a connector, an automated source, or any non-owner (including unresolved/unknown) sender SHALL NOT be counted as engagement, so connector noise cannot poison the disengagement ratchet
- **AND** this check SHALL be lightweight (indexed query) and SHALL NOT delay ingress processing

#### Scenario: Daily attention rollup survives raw-event purge
- **WHEN** the delivery cycle's cleanup step is about to delete `public.insight_engagement` rows older than the retention window
- **THEN** it SHALL first upsert one row per affected day into `public.attention_daily_rollup` (`day`, `insights_delivered`, `insights_engaged`) summarizing that day's delivery/engagement counts
- **AND** the total-disengagement check SHALL fall back to `public.attention_daily_rollup` for any day in its window no longer present in `public.insight_engagement`, so the disengagement ratchet's history is not truncated by the raw-event purge
