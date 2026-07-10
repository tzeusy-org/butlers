## 1. Author the delta

- [x] 1.1 Verify the shipped category-level anomaly behavior against `roster/finance/jobs/finance_jobs.py` `run_insight_scan` step 1 and the constants (`_ANOMALY_THRESHOLD_LOW/MID/HIGH` = 0.30/0.50/1.00, `_SPENDING_ANOMALY_PRIORITY_HIGH/MID/LOW` = 80/65/50), the dedup key `finance:spending-anomaly:{category}:{YYYY-MM}`, end-of-month expiry, and the absence of an explicit cooldown
- [x] 1.2 Write a `## MODIFIED Requirements` delta for `finance-alerts` adding "Category-level spending anomalies via the daily insight scan" under "Automated Periodic Summaries" (siblings carried forward unchanged) and a cross-reference bullet under "Alert Scheduled Task Definitions" → "Daily insight scan schedule"

## 2. Validate and archive

- [x] 2.1 `openspec validate finance-alerts-category-anomaly-scenario --strict` green
- [x] 2.2 `openspec archive finance-alerts-category-anomaly-scenario -y` (merge the delta into the canonical spec)
- [x] 2.3 `openspec validate finance-alerts --strict` green
