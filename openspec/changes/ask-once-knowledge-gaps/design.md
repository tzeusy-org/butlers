## Decisions

- Gaps live in the answering butler's own memory schema (`knowledge_gaps`, `knowledge_gap_origins`);
  no cross-schema SQL. Closure runs on the connection of the fact write, so it commits or rolls
  back with it.
- Capture, closure and the born-answerable re-check serialize on a transaction advisory lock keyed
  on (entity, predicate). A fact write racing a gap insert therefore cannot leave the gap open.
- Origins are rows (`knowledge_gap_origins`), not scalar columns, so two declines merged into one
  open gap each keep their own thread and each receive exactly one notice.
- S1 captures only on the dashboard channel, whose sender is the owner surface. The gap never
  carries content authority: the closing fact's server-stamped authority decides whether the
  notice reads "now known" (owner-class or system) or "reported by <sender>" (any other).
- Dashboard threads are not a `notify` channel. The delivery job posts into
  `public.dashboard_messages` through the same persistence `conversation_reply` uses; other
  channels stay `answerable` and are listed until the Telegram slice lands.
- Delivery is at-least-once under a row lock: the origin row is locked, the message posted, then
  `delivered_at` set in the same transaction. A crash between post and commit can repeat a notice;
  a replayed fact write or a re-run of the job cannot.

## Risks

- Every entity-anchored fact write pays one catalog probe, one advisory lock and one indexed
  UPDATE; the probe skips writes in schemas without the table.
