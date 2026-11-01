# Ambient Projections: Home Assistant and Dumb Panels

> **Scope:** Wiring a wall display, e-ink panel or Home Assistant instance to one
> projection grant's glance.
> **Contract:** `ambient-projections` (REQ-ambient-projections-001 to -004). Grant
> custody and the auth boundary: [Dashboard owner auth](../identity_and_secrets/dashboard-owner-auth.md#ambient-projection-grants).

## Mint one grant per surface

Mint with an owner session (`POST /api/projections`). The response carries the
token once; store it in the surface's own secret store, never in a repo or a
dashboard URL that gets shared.

```json
{"label": "Hall e-ink", "surface_kind": "eink", "audience_class": "household",
 "expected_fetch_interval_seconds": 300}
```

Choose `household` for anything in a shared room: its glance carries only a fixed
label per item kind, never names, amounts or health facts. Set
`expected_fetch_interval_seconds` to the surface's real poll interval. A surface
that misses two intervals shows up in the briefing as "last fetched ... ago".

## Formats

`GET /api/projections/{id}/glance`, token in the `X-Projection-Token` header
(preferred; `?token=` can reach proxy access logs):

| `format` | Response | For |
| --- | --- | --- |
| `json` (default) | `ApiResponse` envelope with `state_class`, `line`, `needs_you`, `top_item`, `as_of`, `valid_until`, `coverage` | Home Assistant, custom displays |
| `text` | One fact per line, ending `Valid until YYYY-MM-DD HH:MM UTC` | Text-only panels, `curl` checks |
| `png` | 1-bit PNG, `width` x `height` (128 to 1600, default 400 x 300); expiry pinned to the bottom edge | E-ink panels that only show images |

Every format prints when the picture stops being true. `valid_until` is `as_of`
plus twice the grant's interval; a surface that cannot refresh shows a visibly
expired picture rather than a stale one. Grant fetches within 30 seconds of each
other share one composition, so a retry or a second format costs one state read.

```bash
curl -fsS -H "X-Projection-Token: $TOKEN" \
  "https://butlers.example/api/projections/$ID/glance?format=png&width=296&height=128" \
  -o glance.png
```

## Home Assistant package

Save as `packages/butlers_glance.yaml` (enable packages with
`homeassistant: packages: !include_dir_named packages`) and put the token in
`secrets.yaml` as `butlers_glance_token`. Replace the host and projection id.

```yaml
rest:
  - resource: https://butlers.example/api/projections/00000000-0000-0000-0000-000000000000/glance
    headers:
      X-Projection-Token: !secret butlers_glance_token
    scan_interval: 300 # match expected_fetch_interval_seconds
    timeout: 20
    sensor:
      - name: Butlers glance
        unique_id: butlers_glance
        value_template: "{{ value_json.data.state_class }}"
        json_attributes_path: "$.data"
        json_attributes:
          - line
          - needs_you
          - top_item
          - as_of
          - valid_until
          - coverage

template:
  - binary_sensor:
      # Edge staleness: on when the last glance has expired or could not be read.
      # HA re-renders templates that call now() every minute, so this turns on
      # even when the REST sensor keeps its last value after a failed poll.
      - name: Butlers glance stale
        unique_id: butlers_glance_stale
        device_class: problem
        state: >
          {% set until = state_attr('sensor.butlers_glance', 'valid_until') %}
          {{ until is none or now() > as_datetime(until) }}
```

Show `line` only while `binary_sensor.butlers_glance_stale` is off; when it is
on, show "Butlers unreachable" instead of the last picture. Rotation is minting a
new grant, updating `secrets.yaml`, then revoking the old grant
(`DELETE /api/projections/{id}`); revoking every grant is the rollback.
