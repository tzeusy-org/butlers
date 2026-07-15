# OwnTracks

OwnTracks supplies the durable phone-location evidence used by Chronicler's
movement and place inference. The connector can be online while that evidence
is too sparse to infer movement, so phone reporting cadence is an operator-owned
part of setup.

## Configure the phone

OwnTracks labels and menu locations vary slightly by platform and app version.
Use the equivalent setting on your device when the wording differs.

1. Keep the existing HTTP connection pointed at the Butlers OwnTracks webhook.
2. Enable extended location data for the connectivity metadata your platform
   supports. On iOS, confirm that a location payload contains the optional
   uppercase `SSID` field while connected to Wi-Fi. OwnTracks does not currently
   document `SSID` as an Android location field, and VPN or OS privacy
   restrictions can prevent it from appearing even on supported devices.
3. Create regions/waypoints named `Home` and `Office` at the corresponding
   locations. Use distinct, stable names: the phone sends them verbatim in the
   location payload's `inregions` list and in enter/leave transitions.
   - iOS: long-press the map at the desired location, then create or edit the
     region and radius.
   - Android: open the app's Regions/Waypoints screen and use the place picker.
4. During waking hours, consider **Move** monitoring mode when movement history
   remains sparse. Move mode requests substantially more frequent/high-power
   location updates and therefore costs more battery; switch back to a lower
   power mode overnight if that tradeoff is preferable.

OwnTracks' official references describe [waypoints and regions](https://owntracks.org/booklet/features/waypoints/),
[location monitoring modes](https://owntracks.org/booklet/features/location/),
and the optional [`SSID` and `inregions` location fields](https://owntracks.org/booklet/tech/json/).

## Verify the evidence stream

After changing the phone configuration:

1. Trigger a manual location publish from OwnTracks.
2. Open **Ingestion → Connectors** and find the phone's OwnTracks identity.
3. Confirm the connector remains online and the sparse-cadence warning clears
   after at least 24 durable location points have arrived in a trailing 24-hour
   window.

The 24-point threshold is a minimum operational baseline, not a claim that 24
points are sufficient for every inference. A warning means the durable evidence
stream is definitely sparse; it does not mean the webhook transport is down.
If the cadence source itself is unavailable, the roster displays a degraded
source note instead of treating the missing diagnostic as an all-clear.

## Label Wi-Fi networks for presence

Chronicler only interprets networks the owner labels explicitly. Store the
mapping in the Chronicler state key `chronicler/owntracks/ssid_places` through
the existing state API:

```bash
curl --request PUT \
  --header 'Content-Type: application/json' \
  --data '{"value":{"Home WiFi":"home","Corp WiFi":"work"}}' \
  http://localhost:41200/api/butlers/chronicler/state/chronicler/owntracks/ssid_places
```

SSID keys are exact and case-sensitive. Place values are limited to `home` and
`work`; malformed mappings safely produce no SSID presence until corrected.
Missing and unlabelled SSIDs are explicit gaps rather than inferred places.

The scheduled projector reads this state on every run. Changing the mapping
causes retained OwnTracks evidence to be replayed idempotently, so an SSID can
be labelled after observations arrive. Removing or relabelling a mapping
tombstones stale derived episodes before replay, so old place claims do not
remain active. The mapping uses the existing JSONB state store and requires no
migration. Projected episodes contain only the canonical `payload.place`; raw
network names remain in the private source evidence and state value.

## Privacy note

OwnTracks location payloads contain precise coordinates and may include the
Wi-Fi network name. For successful durable evidence writes, Butlers preserves
the original accepted location payload in
`connectors.owntracks_points.raw_payload` as durable Chronicler evidence,
including `SSID` and `inregions` when the phone sends them. Restrict database
access accordingly and use SSID/region names that reveal no more than intended.
