# Design

Remove the deploy CLI registration and orchestration module. Move the surviving
ledger pool factory into deployments.py without changing its connection settings.
Reject retired Compose flags during argument parsing, before dotenv, cluster
queries or lifecycle effects. Keep dev and protected restore-drill behavior.
Remove only the observability service overlay; preserve dashboards, paused alert
rules and cluster telemetry configuration. Production deployment receipts retain
the Helm wrapper and the public.deployments writer and readers.

Tests dedicated to the removed pipeline are retired with that behavior. Existing
Helm release/recording and deployment-ledger tests protect surviving behavior;
launcher tests retain dev isolation and test early refusal of old modes. This
behavior change supplies no waiver of separate source-admission instructions.

Historical baseline scenarios remain until normal archive/sync. The explicit
REMOVED delta retires the production pipeline; its retained Helm recording
scenarios move to their own requirement. Old deployment ledger records remain
readable. This does not reopen retired commands or authorize deployment.
