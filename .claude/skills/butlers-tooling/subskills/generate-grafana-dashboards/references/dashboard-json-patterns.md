# Dashboard JSON Patterns

Concrete JSON snippets for Step 4 (Generate JSON) in `SKILL.md`. Load this
when writing or editing a dashboard file's actual structure — variables,
gridPos, units, and the two recurring query patterns.

Use `schemaVersion: 39`. Required top-level structure:

```json
{
  "title": "...",
  "uid": "butlers-<slug>",
  "schemaVersion": 39,
  "refresh": "30s",
  "time": { "from": "now-3h", "to": "now" },
  "templating": { "list": [<datasource-var>, <butler-var>] },
  "panels": [ ... ]
}
```

## Standard variables

Include in every dashboard:

```json
{ "name": "datasource", "type": "datasource", "query": "prometheus", "label": "Datasource" }
```

```json
{
  "name": "butler", "type": "query", "multi": true, "includeAll": true, "label": "Butler",
  "datasource": { "type": "prometheus", "uid": "${datasource}" },
  "query": { "query": "label_values(butlers_spawner_active_sessions{job=\"butlers\"}, butler)" }
}
```

For **Butler Detail**, use single-select (no `includeAll`, no `multi`) so panels focus on
one butler.

**Optional** — include when the dashboard has environment-relevant panels:

```json
{
  "name": "deployment_environment", "type": "query", "multi": false, "includeAll": true,
  "label": "Environment",
  "datasource": { "type": "prometheus", "uid": "${datasource}" },
  "query": { "query": "label_values(target_info{job=\"butlers\"}, deployment_environment)" }
}
```

## Panel sizing and units

**gridPos** (24-column grid): Stat panels `h=4`; Time series `h=8`; Gauge `h=6 w=6`;
Row headers `h=1, w=24`.

**Units**: `"unit": "ms"` for milliseconds, `"percentunit"` for 0–1 ratios, `"reqps"` for
rates, `"short"` for counts.

## Query patterns

**Histogram P95**:

```
histogram_quantile(0.95, sum by(le, butler) (rate(<metric>_bucket{job="butlers", butler=~"$butler"}[$__rate_interval])))
```

**Cumulative total** — for "total over window" panels use a **stat** panel (NOT
timeseries). `increase(...[$__range])` produces a single aggregate value; plotting it as
a timeseries yields a useless flat line:

```json
{
  "type": "stat",
  "options": { "colorMode": "value", "reduceOptions": { "calcs": ["lastNotNull"] } },
  "targets": [{ "expr": "sum by(butler, model) (increase(<counter>{job=\"butlers\", butler=~\"$butler\"}[$__range]))" }]
}
```
