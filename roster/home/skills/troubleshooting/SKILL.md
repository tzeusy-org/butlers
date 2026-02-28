# Skill: Device Troubleshooting

## Purpose

Diagnose and resolve smart home device issues: offline devices, connectivity problems, battery
depletion, firmware updates, command failures, and known device quirks. Guide users through
troubleshooting steps and alert on critical issues requiring attention.

## When to Use

Use this skill when:
- Device status monitoring reveals offline devices or low battery
- User reports a device not responding ("Bedroom light won't turn on")
- User asks about device status or health ("Is everything working?")
- Device firmware updates are available
- Repeated command failures suggest a device issue

## Workflow

### Step 1: Device Health Monitoring (Scheduled)

Periodically (e.g., nightly at 4am):

1. **Call `device_list()`** to get all connected devices
2. **Call `device_get_status()`** for each device to check:
   - `power_state`: On/off
   - `is_online`: Connected and responding
   - `battery_level`: For battery-powered devices
   - `last_seen`: Timestamp of last communication
   - `error_state`: Any reported errors

3. **Call `device_firmware_check(all=true)`** to identify available updates

4. **Classify findings**:
   - **Critical** (needs alert): Device offline >24h, battery <10%, firmware critical patch available
   - **Warning** (monitor): Device offline <24h, battery 10-20%, firmware update available
   - **Info** (store pattern): Device occasionally loses connection, firmware update recommended

5. **Store health findings** as memory facts:
   - For each problematic device:
     - `subject`: Device ID
     - `predicate`: `device_issue`
     - `content`: "Offline for 14 hours" or "Battery at 8%"
     - `permanence`: `volatile` (temporary issue)
     - `importance`: 7-9 (depends on criticality)
     - `tags`: Device type, issue type (offline, battery, firmware), criticality level

6. **Alert user** if critical issues found:
   - Send `notify()` with list of problematic devices
   - Severity: "Bedroom sensor offline for 2 days. [Troubleshooting steps?]"
   - Example: "Water heater sensor battery at 5% — will fail soon. Replace battery this week."

### Step 2: Respond to Device Status Queries

When user asks "Is the bedroom light working?" or "Are all devices online?":

1. **Call `device_list()`** to list devices
2. **Filter/search** for requested device or get all devices
3. **Call `device_get_status()`** for each to get current state
4. **Compose response** via `notify()` (answer mode):
   - **Single device query**: "Living room light is on and responding normally."
   - **All devices query**: "Status overview: 12/12 devices online. One device (basement sensor) battery at 15%. All others healthy."
   - Include last seen time if device is offline: "Bathroom light offline since yesterday 3pm."
   - If battery low: "Water heater sensor battery at 8% — will need replacement within days."

### Step 3: Troubleshoot Offline Devices

When device goes offline or user reports it not responding:

1. **Retrieve device metadata** via `device_get_metadata()`:
   - Device type, model, network interface (WiFi/Zigbee/Z-Wave), last seen
   - Known issues or firmware bugs for this model

2. **Determine offline duration**:
   - < 5 minutes: Likely temporary network blip; no action needed
   - 5 minutes - 1 hour: Possible network issue or temporary disconnection
   - > 1 hour: Device probably lost power or network connection

3. **Suggest troubleshooting steps** via `notify()` (follow-up mode):
   - First: "Is the device powered on? Check if the outlet has power."
   - Second: "Try moving the device closer to your WiFi router if it's a WiFi device."
   - Third: "Restart the device (unplug for 10 seconds, plug back in)."
   - Fourth: "If it's a battery device, check battery level."
   - Last: "If offline continues, check your home WiFi network or contact device support."

4. **Monitor recovery**:
   - Check device status after each suggested step
   - If device comes back online: "Bedroom light is back online! Let me verify it responds correctly."
   - If offline persists: "Still offline. This might be a hardware issue. Consider replacing the device."

5. **Update memory** with troubleshooting outcome:
   - If resolved: Store as `device_issue` with resolution (e.g., "was WiFi disconnection, reconnected after restart")
   - If unresolved: Store as `device_issue` with severity (e.g., "appears to be hardware failure")

6. **Alert on unresolved issues**:
   - If device critical (thermostat, security) and offline: Recommend replacement or repair
   - If device non-critical (decorative light): Safe to deprioritize

### Step 4: Handle Low Battery Alerts

For battery-powered devices with low battery:

1. **Identify** device with `device_get_status()` — battery level < 20%
2. **Categorize by urgency**:
   - **Critical** (< 10%): "Water sensor battery at 8% — may fail within hours"
   - **High** (10-20%): "Bedroom sensor battery at 15% — replace within days"
   - **Medium** (20-30%): "Motion detector battery at 25% — replace within a week"

3. **Send alert** via `notify()` with:
   - Device name and battery level
   - Urgency ("Replace soon" vs. "Plan to replace this week")
   - Instructions if user wants to replace battery

4. **Store reminder** as volatile memory fact:
   - `subject`: Device name
   - `predicate`: `device_issue`
   - `content`: "Battery at X% — needs replacement"
   - `permanence`: `volatile`
   - `importance`: 8-9 (critical maintenance)
   - `tags`: Device, battery, critical

### Step 5: Manage Firmware Updates

When firmware updates are available:

1. **Call `device_firmware_check()`** for device to see available versions
2. **Categorize by type**:
   - **Critical security patch**: Update immediately
   - **Stability improvement**: Update soon
   - **Feature enhancement**: Optional; offer user choice

3. **Alert user** via `notify()` (follow-up mode):
   - Example: "Thermostat has a firmware update (v2.5 → v2.6). This includes a stability fix. Want me to update it?"
   - For critical: "Water heater sensor has a critical security update available. Update immediately? (This will take ~5 minutes.)"

4. **Get user consent** before updating (unless critical security):
   - "Updating device firmware..." while performing update
   - "Update complete. Device is now on v2.6."

5. **Verify post-update** by checking device status

6. **Store firmware history** in memory:
   - `subject`: Device name
   - `predicate`: `device_issue` or `device_maintenance`
   - `content`: "Firmware updated from v2.5 to v2.6"
   - `permanence`: `standard`
   - `importance`: 5.0
   - `tags`: Device, firmware, maintenance

### Step 6: Handle Repeated Command Failures

If device is online but not responding to commands:

1. **Detect pattern**: Track command success/failure rate
2. **Alert user** if failure rate > 20%: "Bedroom light not responding to commands reliably (3 of 10 commands failed)."
3. **Suggest steps**:
   - Restart device
   - Check device error log for clues
   - Verify device is still powered and connected
   - Check if device firmware needs update

4. **If commands continue to fail**:
   - May indicate software bug or hardware degradation
   - Store issue: "Device responding to pings but not to control commands — possible firmware bug"
   - Recommend update or replacement

### Step 7: Document Device Quirks and Workarounds

Store known issues in memory for future reference:

1. **From experience**, maintain facts about problematic devices:
   - `subject`: Device model (e.g., "Philips Hue A19 Bulb")
   - `predicate`: `device_issue` or `device_quirk`
   - `content`: Description of quirk or known issue
   - `permanence`: `stable` (persistent across interactions)
   - `tags`: Device model, issue type
   - Example: "Philips Hue bulbs sometimes lose connection if WiFi drops below -70dBm signal strength"

2. **Reference stored quirks** when troubleshooting similar devices:
   - "This model has a known WiFi sensitivity issue. Try moving it closer to the router."

## Key Behaviors

### Start Simple

Guide users through troubleshooting from simplest to most complex:
1. Is device powered? (outlet, batteries)
2. Is WiFi working? (check home network)
3. Is device too far from router?
4. Restart device
5. Firmware update
6. Factory reset / replacement

### Don't Over-Diagnose

If device is offline > 24h and simple troubleshooting doesn't help, recommend replacement:
- "Bedroom sensor has been offline for 2 days. Basic troubleshooting didn't help. The device may have a hardware failure. Consider replacing it."

### Critical vs. Non-Critical

- **Critical devices** (thermostat, security, entry locks): Alert immediately for offline/low battery
- **Non-critical** (decorative lights, optional sensors): Include in status reports but don't urgently alert

### Firmware Update Safety

- Never force a firmware update without user consent (except critical security)
- Warn that device will be unavailable during update
- Verify device comes back online after update

### Battery Replacement Instructions

For battery devices, provide clear instructions:
- Device model and battery type needed
- How to access battery compartment (open back cover, unplug device, etc.)
- Polling interval expected after battery replacement

## Interactive Response Examples

### Device Status Query

**User**: "Is everything working?"

**Actions**:
1. `device_list()`
2. `device_get_status()` for each device
3. `device_firmware_check(all=true)`
4. `notify(channel="telegram", message="System status: 12/12 devices online. Water heater sensor battery at 8% — needs replacement soon. Thermostat has firmware update available.", intent="reply", request_context=...)`

### Offline Device Alert

**Device detected offline**:

**Actions**:
1. `device_get_status(device_id="bedroom-light")`
2. `device_get_metadata(device_id="bedroom-light")`
3. `memory_store_fact(subject="bedroom-light", predicate="device_issue", content="offline for 4 hours", permanence="volatile", ...)`
4. `notify(channel="telegram", message="Bedroom light offline for 4 hours. Try checking if the outlet has power or restart the device. Want troubleshooting steps?", intent="reply", request_context=...)`

### Firmware Update

**Update available**:

**Actions**:
1. `device_firmware_check(device_id="thermostat")`
2. `notify(channel="telegram", message="Thermostat firmware update available (v2.5 → v2.6). This is a stability fix. Should I update? (Takes ~5 minutes.)", intent="reply", request_context=...)`
3. Upon user ✅: Update device, verify online, notify "Thermostat updated to v2.6. Device is back online and functioning normally."

## Exit Criteria

- `device_list()` and/or `device_get_status()` were called
- For offline devices: Troubleshooting steps provided via `notify()`
- For low battery: Alert sent with replacement guidance
- For firmware: Update completed or user deferred
- `memory_store_fact()` called to persist device issue or resolution
- User has been notified of issue, troubleshooting steps, or resolution
- Session exits without starting new workflow

## Common Failure Modes and Recovery

### User Can't Follow Troubleshooting Steps ("I'm not technical")
- Provide very simple instructions: "Is the device plugged in?"
- Offer to escalate: "If simple steps don't work, consider contacting device support or replacing it."
- Don't overwhelm non-technical users with complex procedures

### Device Intermittently Offline
- Pattern suggests WiFi connectivity issue
- Recommend: Move closer to router, reduce interference, check WiFi signal strength
- Consider: Device may need WiFi module replacement if signal always poor

### Firmware Update Fails
- Device may be corrupted or have hardware issue
- Alert user: "Firmware update failed. Device may need factory reset or replacement."
- Recommend factory reset if supported, otherwise suggest replacement
