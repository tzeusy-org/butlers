# Health Wearable Module Research Draft

Status: **Draft** (Research Only — no implementation)
Last updated: 2026-02-19
Author: Research pass, butlers-962.4
Depends on: `src/butlers/modules/base.py`, `docs/connectors/interface.md`

---

## 1. Purpose

This document captures research into ingesting health and fitness data from
consumer wearable devices — specifically Fitbit, Google Health Connect, and
Apple Health — into the butler framework as a `health_wearable` module.

Primary use cases:
- Periodic ingestion of daily health summaries (steps, heart rate, sleep, HRV, SpO2)
- Intraday granular data collection for trends analysis
- Storing normalised metrics in a butler-owned PostgreSQL database
- Exposing MCP tools for the LLM CLI instance to query health history and
  surface insights (sleep quality, activity streaks, recovery scores)

This is a **research-only** deliverable. No implementation code accompanies
this document. The goal is to document API capabilities, auth models, data
models, integration constraints, and privacy requirements for a future
implementation ticket.

---

## 2. Platform Landscape Overview

Three platforms are assessed for viability as data sources:

| Platform | Access Model | Tailnet-native | Personal free tier | Data freshness |
|---|---|---|---|---|
| **Fitbit Web API** | REST + OAuth 2.0 | Polling only (push needs public endpoint) | Yes, no review required | ~minutes after device sync |
| **Google Health Connect** | Android SDK only (on-device) | Not applicable (no REST) | Yes | Real-time on-device |
| **Apple Health** | On-device HealthKit / manual export | Requires iOS companion or export | Yes | Manual or scheduled push |

**Primary recommendation: Fitbit Web API.** It is the only platform that
provides a standard server-side REST API, supports programmatic polling from
a headless server, offers free personal-use access without approval, and covers
all key health metrics. The other two platforms have fundamental architectural
gaps for a tailnet-resident server-side butler.

---

## 3. Fitbit Web API (Primary Platform)

### 3.1 Overview

The Fitbit Web API is a cloud-hosted REST API that provides access to Fitbit
user health data. Since Google's acquisition of Fitbit in 2021, the API remains
independently maintained at `dev.fitbit.com` and is distinct from the deprecated
Google Fit API.

**Base URL:** `https://api.fitbit.com`
**Documentation:** `https://dev.fitbit.com/build/reference/web-api/`
**Swagger UI:** `https://dev.fitbit.com/build/reference/web-api/explore/`

### 3.2 Auth Model: OAuth 2.0

Fitbit uses OAuth 2.0 with three supported grant flows:

**1. Authorization Code Grant with PKCE (recommended)**

- User visits Fitbit's authorization URL in a browser
- After consent, Fitbit redirects with an `authorization_code` to the
  registered callback URL
- The server exchanges the code for `access_token` + `refresh_token` using
  PKCE (`code_verifier` / `code_challenge`)
- **PKCE is required** for personal-type apps that omit the client secret
- Recommended per RFC 7636 as the most secure flow

**2. Authorization Code Grant (server apps)**

- Requires `client_id` + `client_secret`
- Standard server-to-server exchange
- Used for "Server" application type in Fitbit developer console

**3. Implicit Grant (deprecated)**

- Not recommended; cannot prevent MITM attacks
- Access token expires and requires full re-consent; no refresh token
- Avoid in new integrations

**Token lifecycle:**
- `access_token`: 8-hour lifetime (JWT)
- `refresh_token`: no expiry until used; single-use rotation (new refresh
  token issued with each refresh)
- Token endpoint: `POST https://api.fitbit.com/oauth2/token`
- Refresh endpoint: `POST https://api.fitbit.com/oauth2/token` with
  `grant_type=refresh_token`

**Application types in Fitbit developer console:**
- `Personal`: Access only to the developer's own account. No review required.
  Intraday data automatically accessible. One authorized user only.
- `Client`: For mobile/desktop apps accessing multiple users. 3rd-party
  intraday access requires use-case review and approval.
- `Server`: For server-side apps. Client credentials or auth code flow.

For a self-hosted butler accessing only the owner's Fitbit data, the
`Personal` application type is the correct choice — no approval process,
no use-case review, and intraday access is available immediately.

### 3.3 Rate Limits

- **150 API requests per hour per authorized user**
- Rate limit resets at the top of each clock hour (not a rolling window)
- Exceeded limits return `HTTP 429 Too Many Requests`
- Response headers include `Fitbit-Rate-Limit-Limit`, `Fitbit-Rate-Limit-Remaining`,
  and `Fitbit-Rate-Limit-Reset` (seconds until reset)

**Budget analysis for a daily summary polling pattern:**

A comprehensive daily summary fetch requires approximately 8–12 API calls:
- 1 call: activity summary (steps, distance, calories, floors)
- 1 call: heart rate summary
- 1 call: sleep summary
- 1 call: SpO2 summary
- 1 call: HRV summary
- 1 call: breathing rate summary
- 1 call: skin temperature (Sense/Versa 3+)
- 1 call: body battery/readiness (if applicable)

At 12 calls per daily sync, a cron job running 12× per hour would use all
150 requests. A realistic pattern: 1–4 syncs per hour (12–48 calls) for
daily summaries plus occasional intraday drilldowns. Comfortable within limits.

### 3.4 Polling vs. Subscriptions

**Polling (primary mode for tailnet butler):**
- The butler's scheduler calls Fitbit endpoints on a cron schedule
- Fitbit data is updated whenever the user's device syncs to the mobile app
  (typically several times per day; some users sync daily)
- A 15–30 minute polling interval is sufficient for a personal butler;
  data staleness is primarily gated by device sync frequency, not API polling

**Subscriptions (webhook-based, optional):**
- Fitbit can push notification callbacks when user data is updated
- `POST /1/user/-/apiSubscriptions/<id>.json` to create a subscription
- Notification payload: collection type and user ID; actual data must be
  fetched via separate API call
- **Constraint:** Subscriber endpoint must be publicly reachable via HTTPS
  from Fitbit's servers. Registering the endpoint requires Fitbit to send
  a verification GET request.
- For a tailnet-resident butler with no public ingress, subscriptions require
  either a reverse tunnel (Cloudflare Tunnel, ngrok) or a public DMZ proxy.
- **Recommendation:** Use polling. For a personal use case the 15–30 minute
  polling latency is acceptable. Subscriptions add infrastructure complexity
  (public endpoint) for negligible benefit given that Fitbit data is not truly
  real-time (it depends on the user syncing their device).

### 3.5 Data Model: Key Endpoints and Response Shapes

All endpoints use the pattern:
`GET https://api.fitbit.com/1/user/-/{resource}.json`

The `-` in the user path resolves to the authorized user.

---

#### 3.5.1 Activity (Steps, Calories, Distance, Floors)

**Daily Summary:**
`GET /1/user/-/activities/date/{date}.json`

Key fields in response `summary` object:
```json
{
  "steps": 9342,
  "caloriesOut": 2415,
  "distances": [{"activity": "total", "distance": 7.12}],
  "floors": 12,
  "elevation": 36.58,
  "activeScore": 85,
  "activityCalories": 812,
  "sedentaryMinutes": 680,
  "lightlyActiveMinutes": 185,
  "fairlyActiveMinutes": 23,
  "veryActiveMinutes": 28,
  "marginalCalories": 411
}
```

**Intraday Steps (1-minute resolution):**
`GET /1/user/-/activities/steps/date/{date}/1d/1min.json`

Intraday response includes `activities-steps-intraday.dataset`:
```json
[{"time": "00:00:00", "value": 0}, {"time": "07:30:00", "value": 73}, ...]
```

Available intraday detail levels: `1min`, `15min`.

**Active Zone Minutes (Intraday):**
`GET /1/user/-/activities/active-zone-minutes/date/{date}/1d/1min.json`

---

#### 3.5.2 Heart Rate

**Daily Summary (resting HR):**
`GET /1/user/-/activities/heart/date/{date}/1d.json`

Response includes resting heart rate and heart rate zone distribution:
```json
{
  "activities-heart": [{
    "dateTime": "2026-02-19",
    "value": {
      "restingHeartRate": 58,
      "heartRateZones": [
        {"name": "Out of Range", "min": 30, "max": 98, "minutes": 1180, "caloriesOut": 1690},
        {"name": "Fat Burn", "min": 98, "max": 136, "minutes": 240, "caloriesOut": 612},
        {"name": "Cardio", "min": 136, "max": 163, "minutes": 18, "caloriesOut": 134},
        {"name": "Peak", "min": 163, "max": 220, "minutes": 2, "caloriesOut": 22}
      ]
    }
  }]
}
```

**Intraday Heart Rate (1-minute resolution):**
`GET /1/user/-/activities/heart/date/{date}/1d/1min.json`

Response: `activities-heart-intraday.dataset` with `{time, value}` pairs.
Available detail levels: `1min`, `15min`.

---

#### 3.5.3 Sleep

**Sleep Log:**
`GET /1.2/user/-/sleep/date/{date}.json`

Response `sleep` array, each entry representing a sleep session:
```json
{
  "logId": 1234567890,
  "startTime": "2026-02-18T22:30:00.000",
  "endTime": "2026-02-19T06:45:00.000",
  "duration": 29700000,
  "efficiency": 88,
  "minutesAsleep": 440,
  "minutesAwake": 55,
  "minutesAfterWakeup": 3,
  "minutesToFallAsleep": 12,
  "type": "stages",
  "levels": {
    "summary": {
      "deep": {"count": 4, "minutes": 72, "thirtyDayAvgMinutes": 68},
      "light": {"count": 28, "minutes": 224, "thirtyDayAvgMinutes": 218},
      "rem": {"count": 6, "minutes": 96, "thirtyDayAvgMinutes": 90},
      "wake": {"count": 27, "minutes": 48, "thirtyDayAvgMinutes": 52}
    },
    "data": [
      {"dateTime": "2026-02-18T22:30:30", "level": "wake", "seconds": 60},
      {"dateTime": "2026-02-18T22:31:30", "level": "light", "seconds": 480},
      ...
    ],
    "shortData": [...]
  }
}
```

Sleep stages data has **30-second granularity**. Stage types: `wake`, `light`,
`deep`, `rem`. Devices without stage support return `classic` sleep (just
`asleep`/`awake`/`restless`).

The response `summary` includes:
```json
{
  "stages": {"deep": 72, "light": 224, "rem": 96, "wake": 48},
  "totalMinutesAsleep": 440,
  "totalSleepRecords": 1,
  "totalTimeInBed": 495
}
```

---

#### 3.5.4 SpO2 (Blood Oxygen Saturation)

**Daily Summary:**
`GET /1/user/-/spo2/date/{date}.json`

Response:
```json
{
  "dateTime": "2026-02-19",
  "value": {
    "avg": 96.4,
    "min": 93.0,
    "max": 99.0
  }
}
```

**Intraday SpO2 (by date):**
`GET /1/user/-/spo2/date/{date}/all.json`

Response: `minutes` array with per-minute SpO2 readings:
```json
{
  "dateTime": "2026-02-19",
  "minutes": [
    {"minute": "02:30", "value": 95.7},
    {"minute": "02:31", "value": 96.1},
    ...
  ]
}
```

**Availability:** SpO2 is measured during sleep on supported Fitbit devices
(Charge 4+, Sense, Versa 3+, Inspire 3+). Not available on older devices.

---

#### 3.5.5 Heart Rate Variability (HRV)

**Daily Summary:**
`GET /1/user/-/hrv/date/{date}.json`

Response:
```json
{
  "hrv": [{
    "dateTime": "2026-02-19",
    "value": {
      "dailyRmssd": 38.5,
      "deepRmssd": 43.2,
      "coverage": 0.94,
      "lowFrequency": 62.3,
      "highFrequency": 38.4
    }
  }]
}
```

`dailyRmssd`: Root Mean Square of Successive Differences (overall night).
`deepRmssd`: RMSSD computed during deep sleep specifically.
`coverage`: Fraction of sleep period with valid HRV measurement (0–1).
`lowFrequency` / `highFrequency`: LF/HF power spectral density components.

**Intraday HRV:**
`GET /1/user/-/hrv/date/{date}/all.json`

Returns 5-minute interval HRV readings during sleep.

---

#### 3.5.6 Breathing Rate

**Daily Summary:**
`GET /1/user/-/br/date/{date}.json`

Response:
```json
{
  "br": [{
    "dateTime": "2026-02-19",
    "value": {
      "breathingRate": 16.2
    }
  }]
}
```

Breathing rate is the average breaths per minute during sleep. Measured on
Sense/Versa 3+, Charge 5+, Inspire 3+.

---

#### 3.5.7 Skin Temperature

**Daily Summary:**
`GET /1/user/-/temp/skin/date/{date}.json`

Response:
```json
{
  "tempSkin": [{
    "dateTime": "2026-02-19",
    "value": {
      "nightlyRelative": 0.3,
      "deviceType": "fitbit-sense"
    }
  }]
}
```

`nightlyRelative`: Nightly skin temperature deviation from personal baseline
in °C. Available on Sense and Sense 2 only.

---

#### 3.5.8 Cardio Fitness Score (VO2 Max)

**Endpoint:**
`GET /1/user/-/cardioscore/date/{date}.json`

Response:
```json
{
  "cardioScore": [{
    "dateTime": "2026-02-19",
    "value": {
      "vo2Max": "42-46"
    }
  }]
}
```

VO2 Max is estimated and returned as a range string. Available on devices
with GPS or GPS + HR (Charge 4+, Sense, Ionic).

---

#### 3.5.9 Body Weight and BMI

`GET /1/user/-/body/log/weight/date/{date}.json`

Returns manual weight log entries. Not automated from a wearable sensor;
requires user input in the Fitbit app. Useful if the user manually logs weight.

---

### 3.6 Endpoint Summary Table

| Metric | Endpoint (base: `/1/user/-/`) | Intraday | Device requirement |
|---|---|---|---|
| Steps | `activities/steps/date/{date}/1d.json` | 1min, 15min | All Fitbit |
| Calories | `activities/date/{date}.json` | 1min | All Fitbit |
| Active Zones | `activities/active-zone-minutes/...` | 1min | All Fitbit |
| Resting HR | `activities/heart/date/{date}/1d.json` | 1min | HR-capable devices |
| Sleep stages | `sleep/date/{date}.json` (/1.2/) | 30 sec | Most modern Fitbits |
| SpO2 | `spo2/date/{date}.json` | Per-minute | Charge 4+, Sense, Versa 3+, Inspire 3+ |
| HRV | `hrv/date/{date}.json` | 5min | Sense, Charge 5+, Versa 3+ |
| Breathing rate | `br/date/{date}.json` | — | Sense, Charge 5+, Versa 3+, Inspire 3+ |
| Skin temperature | `temp/skin/date/{date}.json` | — | Sense, Sense 2 only |
| VO2 Max | `cardioscore/date/{date}.json` | — | Charge 4+, Sense, Ionic |

---

### 3.7 Python Ecosystem

**python-fitbit** (`orcasgit/python-fitbit`, MIT):
- Wraps OAuth 2.0 token management with automatic refresh
- `Fitbit` client class; `token_updater` callback for persistent token storage
- `gather_keys_oauth2.py` helper script spins up a local CherryPy server on
  `127.0.0.1:8080` to capture the OAuth redirect for initial token acquisition
- Not async-native; requires `asyncio.to_thread()` wrapping or replacement

**fitbit-web-api** (`chemelli74/fitbit-web-api`, MIT):
- Generated from Fitbit's Swagger spec
- Provides typed models for all endpoints
- More complete API coverage than `python-fitbit`

**Async alternative:**
- Use `httpx` or `aiohttp` directly with PKCE OAuth flow
- Store token in PostgreSQL JSONB column, implement refresh on 401
- More idiomatic for the butler's `asyncio` stack

**Recommendation:** For the butler module, implement OAuth flow directly with
`httpx` (already likely a dependency) and store tokens in PostgreSQL. The
`python-fitbit` library's synchronous design is a friction point in an asyncio
codebase. A thin async wrapper around the Fitbit REST API is straightforward
given the limited number of endpoints needed.

---

## 4. Google Health Connect (Android Platform)

### 4.1 Overview and Architecture

Google Health Connect is the Android-native health data platform that replaced
the deprecated Google Fit APIs. It provides a unified on-device data store
that aggregates health data from Samsung Health, Google Fit, Fitbit (via the
Fitbit app), and other fitness apps.

**Key architectural property:** Health Connect is **entirely on-device**. Data
is stored locally on the Android device, not in Google's cloud. There is no
server-side REST API.

**Timeline:**
- Android 9+ (via Play Store app): Health Connect available since October 2022
- Android 14+ (API Level 34): Health Connect is part of the Android Framework
  (no separate app install needed)
- Google Fit REST API: Deprecated May 1, 2024; shut down June 30, 2025
- Google Fit Android SDK: Deprecated in 2024, migration to Health Connect recommended
- Android 16: Health Connect adds medical data (FHIR format) starting with
  immunization records

### 4.2 Access Model

Access to Health Connect data requires:
1. A **native Android app** (Kotlin/Java)
2. The Health Connect Jetpack SDK: `androidx.health:health-connect-client`
3. Health Connect **permissions** declared in the app's `AndroidManifest.xml`
  and granted by the user at runtime
4. App signed and distributed (via Play Store or sideloading)

**No REST API exists.** Health Connect provides no server-side endpoint for
accessing data programmatically from a non-Android process. The only access
path is through the Android SDK running on the same device where the data is
stored.

For the butler (a Python server daemon), this means:
- **Direct Health Connect integration is not possible from a server context.**
- A companion Android app would need to read data from Health Connect and
  push it to the butler via an outbound HTTP call (reverse of the normal
  connector pattern).

### 4.3 Supported Data Types

Health Connect organizes data into record types with standardized schemas:

| Category | Record Types |
|---|---|
| Activity | `StepsRecord`, `DistanceRecord`, `ActiveCaloriesBurnedRecord`, `ExerciseSessionRecord`, `SpeedRecord`, `PowerRecord` |
| Heart | `HeartRateRecord`, `RestingHeartRateRecord`, `HeartRateVariabilityRmssdRecord` |
| Sleep | `SleepSessionRecord`, `SleepStageRecord` |
| Vitals | `OxygenSaturationRecord`, `RespiratoryRateRecord`, `BloodPressureRecord`, `BloodGlucoseRecord` |
| Body | `WeightRecord`, `HeightRecord`, `BodyFatRecord`, `BoneMassRecord` |
| Nutrition | `NutritionRecord`, `HydrationRecord` |

Data from multiple sources (apps) is unified under a common schema. A
`StepsRecord` has the same structure regardless of whether it came from
Samsung Health, Fitbit, or a third-party running app.

### 4.4 Permissions Model

Health Connect uses Android runtime permissions. Each data type requires a
separate `READ_*` permission (and `WRITE_*` for writes). Examples:
- `android.permission.health.READ_STEPS`
- `android.permission.health.READ_HEART_RATE`
- `android.permission.health.READ_SLEEP`
- `android.permission.health.READ_OXYGEN_SATURATION`

Permissions must be declared in `AndroidManifest.xml` and requested at
runtime. Google Play requires apps that access Health Connect data to
disclose their usage via privacy policy.

### 4.5 Viability Assessment for Butler Integration

| Factor | Assessment |
|---|---|
| Server-side access | Not possible — on-device SDK only |
| Headless operation | Not supported |
| Python compatibility | No — Kotlin/Java SDK only |
| Tailnet fit | N/A (data never leaves the Android device via Health Connect) |
| Requires Android device | Yes, always |
| Data richness | Excellent (unified from all Android health apps) |
| Cost | Free, no API key |

**Verdict: Not viable as a direct server-side integration target.** Health
Connect is architecturally incompatible with the butler's server-side Python
daemon model. Integration is only possible via a companion Android app that
bridges Health Connect to the butler.

### 4.6 Companion App Bridge Pattern (Future Extension)

A lightweight Android companion app could:
1. Acquire Health Connect READ permissions
2. Run a background sync job (e.g., `WorkManager` periodic task)
3. POST normalized health data to the butler's webhook ingestion endpoint
   via outbound HTTPS over the tailnet (Tailscale on Android is supported)

This pattern mirrors the Apple Health companion approach (see section 5.3).
The butler module would then receive data via a webhook handler rather than
actively polling.

This is not in scope for the initial `health_wearable` module but is
documented here as the viable path if Health Connect data is desired.

---

## 5. Apple Health

### 5.1 Overview and Architecture

Apple Health (HealthKit) stores health and fitness data on an iPhone.
The data is local — it does not sync to Apple servers in an accessible form.
HealthKit is the iOS/macOS framework for reading and writing health data.

**Key architectural property:** No server-side REST API exists. Access to
Apple Health data requires a **native iOS/macOS application** that has been
granted HealthKit permissions.

### 5.2 Access Paths

**5.2.1 Manual Export (export.xml)**

Users can export all their Apple Health data from the iPhone Health app:
Settings → Health → Export All Health Data → generates a ZIP containing:
- `export.xml`: Primary data file (can exceed 2 GB for long-term users)
- `export_cda.xml`: CDA-format lab results (if any)
- `workout-routes/`: GPX files for workout GPS routes

**XML structure:**
```xml
<HealthData locale="en_US">
  <ExportDate value="2026-02-19 08:00:00 +0000"/>
  <Me HKCharacteristicTypeIdentifierDateOfBirth="..." .../>
  <Record type="HKQuantityTypeIdentifierStepCount"
          sourceName="Apple Watch"
          sourceVersion="10.3"
          device="Apple Watch"
          unit="count"
          creationDate="2026-02-19 07:30:00 +0100"
          startDate="2026-02-19 07:29:00 +0100"
          endDate="2026-02-19 07:30:00 +0100"
          value="84"/>
  <Record type="HKCategoryTypeIdentifierSleepAnalysis"
          value="HKCategoryValueSleepAnalysisAsleepREM"
          startDate="..."
          endDate="..."/>
  <Workout workoutActivityType="HKWorkoutActivityTypeRunning"
           duration="32.4" durationUnit="min"
           totalDistance="5.12" totalDistanceUnit="km"
           .../>
</HealthData>
```

**Parsing challenges:**
- File size can be 2 GB+ — streaming XML parsing required (`xml.etree.ElementTree.iterparse`)
- Apple uses verbosely-named identifiers (e.g., `HKQuantityTypeIdentifierHeartRateVariabilitySDNN`)
- DTD is embedded (not URL-referenced), which can cause issues with strict XML parsers
- No API version — format changes with iOS updates without documentation

**Relevant HealthKit type identifiers:**

| Metric | HK Identifier |
|---|---|
| Steps | `HKQuantityTypeIdentifierStepCount` |
| Heart rate | `HKQuantityTypeIdentifierHeartRate` |
| Resting HR | `HKQuantityTypeIdentifierRestingHeartRate` |
| HRV (SDNN) | `HKQuantityTypeIdentifierHeartRateVariabilitySDNN` |
| SpO2 | `HKQuantityTypeIdentifierOxygenSaturation` |
| Respiratory rate | `HKQuantityTypeIdentifierRespiratoryRate` |
| Sleep analysis | `HKCategoryTypeIdentifierSleepAnalysis` |
| Active energy | `HKQuantityTypeIdentifierActiveEnergyBurned` |
| VO2 Max | `HKQuantityTypeIdentifierVO2Max` |
| Body temperature | `HKQuantityTypeIdentifierBodyTemperature` |
| Blood oxygen sat | `HKQuantityTypeIdentifierOxygenSaturation` |

**Limitation:** The manual export is a one-time snapshot. Automating it
requires iPhone interaction (no headless trigger path via iOS).

---

**5.2.2 Automated Export via Companion App**

Third-party iOS apps can read HealthKit data and push it to external
endpoints. Notable options:

**Health Auto Export** (App Store, freemium):
- Reads 150+ health metrics from HealthKit
- Exports as CSV, JSON, or GPX on a configurable schedule
- Supports push to REST API endpoints, MQTT, Home Assistant, Dropbox,
  Google Drive, and iCloud Drive
- Can be configured to POST to the butler's webhook ingestion endpoint

**iOS Shortcuts automation:**
- Apple Shortcuts can read HealthKit data and make HTTP POST requests
- Schedulable via the Shortcuts Automations tab (runs on iOS every 2 hours
  minimum, or on triggers like "When I arrive home")
- Limitation: **Cannot run in the background while the phone is locked.**
  The iPhone must be unlocked for the Shortcut to execute and HealthKit to
  return data.

**Custom iOS app:**
- Requires Xcode, Apple Developer account ($99/year), and ongoing app signing
- Full control over which data types, sync interval, and format
- Most reliable long-term solution; highest implementation cost

**Recommended path for Apple Health integration:**
Use the **Health Auto Export** app (or equivalent) configured to push to the
butler's webhook endpoint at a regular interval. This requires:
1. The butler exposes an HTTP webhook endpoint on the tailnet
2. Health Auto Export on the iPhone sends data via HTTP POST to that endpoint
3. The butler module receives and stores the data

**Tailnet constraint:** The iPhone must be on the tailnet (Tailscale app for
iOS is available and free). With Tailscale, the iPhone can POST to the butler's
tailnet IP/FQDN without a public internet endpoint.

### 5.3 Viability Assessment

| Factor | Assessment |
|---|---|
| Server-side REST API | Does not exist |
| Headless operation | Not supported (iPhone must be unlocked for Shortcuts) |
| Companion app bridge | Viable (Health Auto Export or custom iOS app) |
| Python server code | N/A (data arrives via webhook push) |
| Tailnet fit | Viable via Tailscale on iOS |
| Data richness | Excellent (all Apple Watch + manual health data) |
| Cost | Free (iOS app) or $99/year (Apple Developer for custom app) |
| Automation reliability | Medium (background execution limited by iOS) |

**Verdict: Viable as a push-only webhook source, not as a polled REST source.**
The butler module would need to expose a webhook receiver. Apple Health data
arrives asynchronously pushed from the user's iPhone. No active polling from
the server side is possible.

---

## 6. Unified Data Model

### 6.1 Rationale

Fitbit, Apple Health, and Health Connect use incompatible naming conventions,
time representations, and unit systems. A normalised internal schema isolates
the butler's storage and MCP tools from platform-specific quirks and allows
future addition of other wearables (Oura, Garmin, Withings, etc.) without
schema changes.

### 6.2 Proposed Schema

The unified schema uses named tables per metric category rather than a single
wide table, following PostgreSQL JSONB best practices already established in
the butler codebase.

```sql
-- Source platform registrations
CREATE TABLE hw_sources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        TEXT NOT NULL,          -- 'fitbit', 'apple_health', 'health_connect'
    display_name    TEXT,                   -- e.g. "My Charge 6"
    user_id         TEXT,                   -- platform-specific user identifier
    config          JSONB NOT NULL DEFAULT '{}'::JSONB,  -- OAuth tokens (encrypted), sync state
    last_synced_at  TIMESTAMPTZ,
    active          BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Daily activity summaries (one row per platform per calendar day)
CREATE TABLE hw_activity_daily (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES hw_sources(id),
    date            DATE NOT NULL,
    steps           INTEGER,
    distance_m      FLOAT,          -- metres
    calories_out    FLOAT,          -- kcal
    calories_active FLOAT,          -- kcal (activity-only, no BMR)
    floors          INTEGER,
    elevation_m     FLOAT,
    sedentary_min   INTEGER,
    lightly_active_min INTEGER,
    fairly_active_min  INTEGER,
    very_active_min    INTEGER,
    active_zone_min    INTEGER,     -- Fitbit-specific AZM
    raw             JSONB,          -- full platform response preserved
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, date)
);

-- Daily heart rate summaries
CREATE TABLE hw_heart_rate_daily (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES hw_sources(id),
    date            DATE NOT NULL,
    resting_bpm     INTEGER,
    zone_out_of_range_min INTEGER,
    zone_fat_burn_min     INTEGER,
    zone_cardio_min       INTEGER,
    zone_peak_min         INTEGER,
    raw             JSONB,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, date)
);

-- Intraday heart rate (1-minute resolution)
CREATE TABLE hw_heart_rate_intraday (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES hw_sources(id),
    recorded_at     TIMESTAMPTZ NOT NULL,   -- UTC
    bpm             INTEGER NOT NULL,
    confidence      FLOAT,                  -- 0-1 if available
    UNIQUE (source_id, recorded_at)
);

-- Daily sleep summaries
CREATE TABLE hw_sleep_daily (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES hw_sources(id),
    date            DATE NOT NULL,          -- date of the night (morning date)
    sleep_start     TIMESTAMPTZ,
    sleep_end       TIMESTAMPTZ,
    duration_min    INTEGER,                -- total time in bed
    asleep_min      INTEGER,               -- total minutes asleep
    awake_min       INTEGER,
    light_min       INTEGER,               -- light NREM
    deep_min        INTEGER,               -- deep NREM (N3)
    rem_min         INTEGER,               -- REM
    efficiency_pct  INTEGER,               -- sleep efficiency %
    sessions        INTEGER DEFAULT 1,     -- number of sleep sessions
    raw             JSONB,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, date)
);

-- Sleep stage timeline (granular)
CREATE TABLE hw_sleep_stages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES hw_sources(id),
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ NOT NULL,
    stage           TEXT NOT NULL CHECK (stage IN ('wake', 'light', 'deep', 'rem', 'asleep', 'restless')),
    duration_s      INTEGER NOT NULL,
    UNIQUE (source_id, started_at)
);

-- SpO2 (daily summaries)
CREATE TABLE hw_spo2_daily (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES hw_sources(id),
    date            DATE NOT NULL,
    avg_pct         FLOAT,
    min_pct         FLOAT,
    max_pct         FLOAT,
    raw             JSONB,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, date)
);

-- Intraday SpO2 (per-minute during sleep)
CREATE TABLE hw_spo2_intraday (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES hw_sources(id),
    recorded_at     TIMESTAMPTZ NOT NULL,
    pct             FLOAT NOT NULL,
    UNIQUE (source_id, recorded_at)
);

-- HRV daily summaries (sleep-derived)
CREATE TABLE hw_hrv_daily (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES hw_sources(id),
    date            DATE NOT NULL,
    rmssd           FLOAT,          -- overall night RMSSD (ms)
    deep_rmssd      FLOAT,          -- RMSSD during deep sleep
    coverage        FLOAT,          -- fraction of sleep with valid HRV (0-1)
    lf_power        FLOAT,          -- low-frequency power (ms^2)
    hf_power        FLOAT,          -- high-frequency power (ms^2)
    sdnn            FLOAT,          -- Apple Health uses SDNN; Fitbit uses RMSSD
    raw             JSONB,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, date)
);

-- Breathing rate (nightly average)
CREATE TABLE hw_breathing_rate_daily (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES hw_sources(id),
    date            DATE NOT NULL,
    breaths_per_min FLOAT NOT NULL,
    raw             JSONB,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, date)
);

-- Readiness / wellness scores (composite)
CREATE TABLE hw_readiness_daily (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES hw_sources(id),
    date            DATE NOT NULL,
    vo2_max_min     INTEGER,        -- VO2 Max range lower bound
    vo2_max_max     INTEGER,        -- VO2 Max range upper bound
    skin_temp_relative FLOAT,       -- °C deviation from baseline (Fitbit Sense)
    raw             JSONB,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, date)
);

-- Sync cursor / checkpoint per source
CREATE TABLE hw_sync_cursors (
    source_id       UUID PRIMARY KEY REFERENCES hw_sources(id),
    last_synced_date DATE NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Design notes:**
- Each metric table has `UNIQUE (source_id, date)` (or `recorded_at` for
  intraday) to support idempotent upserts on re-sync
- `raw JSONB` column preserves the full platform response for future
  schema evolution without migration
- `hw_sources.config JSONB` stores encrypted OAuth tokens; the butler's
  existing state store encryption should be applied
- Times are stored as `TIMESTAMPTZ` in UTC; all normalization happens at
  ingest time

### 6.3 Unified Metric Naming

| Canonical field | Fitbit source | Apple Health source | Health Connect source |
|---|---|---|---|
| `steps` | `summary.steps` | `HKQuantityTypeIdentifierStepCount` | `StepsRecord.count` |
| `resting_bpm` | `value.restingHeartRate` | `HKQuantityTypeIdentifierRestingHeartRate` | `RestingHeartRateRecord.beatsPerMinute` |
| `asleep_min` | `summary.totalMinutesAsleep` | sleep analysis records (sum of asleep stages) | `SleepSessionRecord` |
| `deep_min` | `levels.summary.deep.minutes` | `HKCategoryValueSleepAnalysisAsleepDeep` duration | `SleepStageRecord(STAGE_DEEP)` |
| `rmssd` | `value.dailyRmssd` | `HKQuantityTypeIdentifierHeartRateVariabilitySDNN` | `HeartRateVariabilityRmssdRecord` |
| `avg_pct` (SpO2) | `value.avg` | `HKQuantityTypeIdentifierOxygenSaturation` | `OxygenSaturationRecord` |
| `breaths_per_min` | `value.breathingRate` | `HKQuantityTypeIdentifierRespiratoryRate` | `RespiratoryRateRecord` |

**Note on HRV metric discrepancy:**
- Fitbit reports **RMSSD** (Root Mean Square of Successive Differences)
- Apple Health / HealthKit reports **SDNN** (Standard Deviation of NN intervals)
- These are related but distinct metrics; both are stored in `hw_hrv_daily`
  with separate columns (`rmssd` and `sdnn`) so they are not conflated

---

## 7. Butler Integration Points

### 7.1 Module Architecture

The `health_wearable` module implements the `Module` ABC:

```python
class HealthWearableModule(Module):
    name = "health_wearable"
    dependencies = []  # standalone; no pipeline dependency for data ingestion

    async def register_tools(self, mcp, config, db) -> None:
        # MCP tools exposed to the LLM CLI:
        # bot_hw_get_daily_summary(date: str) -> dict
        # bot_hw_get_sleep_detail(date: str) -> dict
        # bot_hw_get_heart_rate_intraday(date: str) -> list[dict]
        # bot_hw_get_spo2_history(start: str, end: str) -> list[dict]
        # bot_hw_get_hrv_trend(days: int) -> list[dict]
        # bot_hw_get_readiness_score(date: str) -> dict
        # bot_hw_sync_now() -> dict  # manual trigger for immediate sync
        # user_hw_connect_fitbit() -> str  # initiate OAuth flow (returns auth URL)
        # user_hw_disconnect_platform(platform: str) -> dict

    async def migrations(self) -> str | None:
        return "health_wearable"

    async def on_startup(self, config, db) -> None:
        # Register cron tasks for periodic polling
        # Default: sync daily summaries every 30 minutes

    async def on_shutdown(self) -> None:
        # Cancel background sync tasks
```

### 7.2 Fitbit OAuth Flow in a Headless Butler

The initial OAuth pairing requires a browser interaction. For a tailnet-resident
butler without a web browser, the following flow is recommended:

1. Butler exposes an MCP tool `user_hw_connect_fitbit()` that:
   a. Generates a PKCE `code_verifier` and `code_challenge`
   b. Constructs the Fitbit authorization URL with scopes and redirect URI
   c. Returns the URL to the LLM CLI, which presents it to the user
2. User opens the URL in their browser (tailnet browser or any browser),
   authorizes the app, and is redirected to the butler's callback URL
3. The callback URL must be accessible from the user's browser — either:
   - A local redirect to `http://localhost:8080/callback` captured by a
     short-lived local HTTP server (if the user is on the same machine)
   - A tailnet URL (e.g., `http://butler.tailnet.ts.net/hw/callback`) served
     by the butler's FastMCP/FastAPI HTTP layer
4. Butler exchanges the authorization code for tokens and stores them
   encrypted in `hw_sources.config`

**Redirect URI options:**

| Option | Pros | Cons |
|---|---|---|
| `http://localhost:8080/callback` | Standard, no public exposure | Only works if user runs butler on same machine |
| Tailnet FQDN (Tailscale DNS) | Accessible from any tailnet device | Requires HTTPS (Tailscale HTTPS certs available) |
| `urn:ietf:wg:oauth:2.0:oob` | No redirect server needed | Deprecated; Fitbit may not support |

**Recommended:** Tailnet HTTPS redirect URI using Tailscale machine certificates.
The butler's FastAPI layer serves the OAuth callback on the tailnet FQDN.

### 7.3 Sync Scheduler Design

The butler's built-in cron scheduler drives data ingestion:

```
Schedule: every 30 minutes (configurable)
Task: hw_daily_sync

Procedure:
1. For each active source in hw_sources:
   a. Determine date range to sync (last_synced_date → today)
   b. Fetch each metric for each date:
      - activity summary
      - heart rate summary
      - sleep summary
      - SpO2 summary
      - HRV summary
      - breathing rate
   c. Upsert into respective hw_* tables
   d. Update hw_sync_cursors.last_synced_date
   e. Respect 150 req/hr rate limit:
      - Track request count per hour with a simple Redis/state-store counter
      - Insert 500 ms delay between calls for politeness
      - Exponential backoff on 429 responses
```

**Backfill on first connect:** When a new Fitbit source is connected,
the module should offer to backfill historical data. Fitbit's API allows
requesting data for any date, but intraday data is limited to the API's
stated intraday access scope. Backfill should be rate-limited to stay within
the 150/hour limit (12 calls × 12 days = 144 calls ≈ 1 hour per 12 days of
history).

### 7.4 MCP Tool Interface

All tools use the `bot_hw_*` prefix (bot-identity, no user approval needed
for reads). The `user_hw_*` tools (OAuth pairing) require user identity
because they initiate privileged auth flows.

**`bot_hw_get_daily_summary(date: str) -> dict`**
- Returns all daily metrics for a given date in a single response
- Aggregates from `hw_activity_daily`, `hw_heart_rate_daily`,
  `hw_sleep_daily`, `hw_spo2_daily`, `hw_hrv_daily`, `hw_breathing_rate_daily`
- Date format: ISO 8601 (`YYYY-MM-DD`); `"today"` alias supported

**`bot_hw_get_sleep_detail(date: str) -> dict`**
- Returns sleep session with stage breakdown and timeline
- Fetches from `hw_sleep_daily` + `hw_sleep_stages`

**`bot_hw_get_hrv_trend(days: int = 14) -> list[dict]`**
- Returns HRV (RMSSD) values for the past N days
- Supports trend analysis by the LLM

**`bot_hw_sync_now() -> dict`**
- Triggers an immediate sync outside the regular schedule
- Returns: `{synced_dates: [...], metrics_fetched: int, errors: [...]}`
- Rate-limit aware: aborts and reports if budget is near-exhausted

---

## 8. Rate Limit and Polling Strategy

### 8.1 Budget Allocation

**Fitbit rate limit:** 150 calls/hour (resets at top of clock hour)

**Daily summary sync (8 metric types × 1 date = 8 calls per day per sync):**

| Polling interval | Calls per hour | % budget used |
|---|---|---|
| Every 60 min | 8 | 5.3% |
| Every 30 min | 16 | 10.7% |
| Every 15 min | 32 | 21.3% |
| Every 10 min | 48 | 32% |

**Recommendation:** 30-minute polling for daily summaries. This leaves 134
calls/hour budget for intraday fetches, manual sync triggers, and future
expansion.

**Intraday budget (when requested by LLM):**
- 1 day of intraday heart rate (1-minute): 1 call
- 1 day of intraday SpO2: 1 call
- 1 day of intraday steps: 1 call
- 1 day of intraday HRV: 1 call
Total: 4 calls per day of intraday data

### 8.2 Rate Limit Implementation

```python
# State store key: "hw_fitbit_calls_this_hour"
# Reset logic: compare current hour (UTC) to stored hour
async def check_rate_limit(db, calls_needed: int) -> bool:
    state = await db.kv_get("hw_fitbit_rate_limit")
    current_hour = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    if state and state["hour"] == current_hour.isoformat():
        remaining = 150 - state["calls_used"]
    else:
        remaining = 150
    return remaining >= calls_needed

async def record_api_calls(db, count: int) -> None:
    # Upsert hw_fitbit_calls_this_hour in state store
    ...
```

### 8.3 Retry and Backoff

On `HTTP 429`:
1. Read `Retry-After` header (seconds until reset)
2. Sleep until reset + 5-second jitter
3. Retry the failed request once
4. If still 429, abort sync and schedule retry at next cron tick

On `HTTP 401` (token expired):
1. Attempt token refresh using stored refresh token
2. If refresh succeeds, store new tokens, retry request
3. If refresh fails (token revoked), mark source as `needs_reauth` and
   notify via Telegram/notification channel

---

## 9. Privacy and Encryption Requirements

### 9.1 Data Classification

Health and fitness data from wearables occupies a sensitive category under
multiple regulatory frameworks:

**GDPR (EU):**
- "Data concerning health" is a **Special Category** under GDPR Article 9
- Requires explicit consent for processing
- Must document the legal basis for processing (legitimate interest of the
  user accessing their own data is the appropriate basis for a personal butler)
- Right to erasure applies: `user_hw_delete_data()` MCP tool should be
  implemented to purge all stored health data from the database

**HIPAA (US):**
- Fitbit and Apple Health in consumer context are **not HIPAA covered entities**
- HIPAA applies only when a Covered Entity (healthcare provider, health plan)
  processes the data
- A personal self-hosted butler is not a covered entity; HIPAA does not apply
  in the technical regulatory sense
- However, adopting **HIPAA-adjacent security controls** is a best practice
  given the sensitivity of the data: encryption at rest, access logging, minimal
  retention

**FTC Health Breach Notification Rule (US, effective July 29, 2024):**
- Applies to apps and platforms collecting health data, including fitness apps
- Requires breach notification to consumers and the FTC within 60 days
- A personal self-hosted butler does not qualify as a "vendor of personal health
  records" under the rule (it processes only the owner's own data with no
  commercial element)
- No obligation, but awareness is warranted

### 9.2 OAuth Token Storage Security

Fitbit OAuth tokens provide access to all user health data. They must be
stored with encryption:

- Store `access_token` and `refresh_token` in `hw_sources.config JSONB`
- Encrypt the config column at the application level before writing to
  PostgreSQL (the butler's existing state-store encryption should be applied)
- Never log access tokens or refresh tokens
- Never return raw token values via MCP tools

**Key rotation:** When Fitbit issues a new refresh token on token refresh, the
old token is immediately invalidated. Store the new tokens synchronously before
using them.

### 9.3 Data Minimization

- Store only the metrics explicitly configured by the user (default: all
  summary metrics; intraday is opt-in)
- Intraday data (per-minute heart rate, SpO2) is high-volume; configurable
  retention window (default: 90 days for intraday, 5 years for daily summaries)
- Provide `user_hw_delete_data(platform: str, before_date: str)` MCP tool for
  user-initiated purge

### 9.4 Encryption at Rest

- PostgreSQL databases are butler-owned (architectural constraint)
- Apply PostgreSQL transparent data encryption (TDE) at the volume level for
  the butler host (managed by the Docker/OS layer)
- Sensitive fields (`config` in `hw_sources`) should additionally be
  application-level encrypted using Fernet or AES-GCM with a key stored in
  the butler's secrets config

### 9.5 Audit Logging

- Every API call to Fitbit should be logged to the butler's session log with:
  timestamp, endpoint, HTTP status, response latency
- Health data queries via MCP tools should be logged (the butler's session log
  already provides this for all tool calls)
- No sensitive values (raw token values, actual health metrics in logs at
  DEBUG level in production)

---

## 10. Platform Comparison and Recommendation

| Criterion | Fitbit Web API | Google Health Connect | Apple Health |
|---|---|---|---|
| Server-side REST API | Yes | No | No |
| Personal use, free tier | Yes (no review) | Yes (Android SDK) | Yes (via companion app) |
| Tailnet-native polling | Yes | No (on-device only) | No (push from iPhone) |
| Data richness | High (steps, HR, sleep, SpO2, HRV, BR) | Very high (unified from all Android apps) | Very high (all Apple Watch + manual data) |
| Auth model | OAuth 2.0 PKCE | Android runtime permissions | HealthKit permissions (iOS app) |
| Rate limits | 150 req/hr | N/A (local SDK) | N/A (push model) |
| Python SDK | python-fitbit (MIT) + direct httpx | No (Kotlin/Java only) | No |
| Intraday data | Yes (1-min, auto for Personal apps) | Yes (on-device) | Yes (in export) |
| HRV | RMSSD (Sense/Charge 5+) | RMSSD record | SDNN (Apple Watch) |
| Sleep stages | Yes (30-sec granularity) | Yes | Yes (NREM/REM stages) |
| SpO2 | Yes (Charge 4+, Sense, Versa 3+) | Yes | Yes (Apple Watch Series 6+) |
| Privacy posture | Google-owned (Fitbit data committed not used for ads) | On-device (Google account-associated) | On-device (iCloud backup optional) |
| Implementation complexity | Low–Medium | High (companion app) | Medium (companion app) |

**Recommendation order:**
1. **Fitbit Web API** — primary target; only platform with direct server-side
   REST polling, free personal access, full metric coverage, Python ecosystem
2. **Apple Health** (via companion app) — secondary; high value for iPhone/
   Apple Watch users; requires a push bridge (Health Auto Export or custom app)
3. **Google Health Connect** (via companion app) — tertiary; viable for
   Android users; requires a dedicated Android companion app

---

## 11. Butler Configuration Schema

Proposed `butler.toml` configuration block for the health_wearable module:

```toml
[modules.health_wearable]
enabled = true
platforms = ["fitbit"]          # platforms to activate: ["fitbit", "apple_health", "health_connect"]
poll_interval_min = 30          # polling interval for Fitbit (minutes)
backfill_days = 90              # how many days of history to backfill on first connect
intraday_enabled = false        # whether to fetch per-minute intraday data (high volume)
intraday_metrics = ["heart_rate", "spo2"]  # which intraday metrics to collect (if enabled)
intraday_retention_days = 90    # retention for intraday data (daily summaries retained longer)
daily_retention_years = 5       # retention for daily summaries

[modules.health_wearable.fitbit]
client_id = ""                  # populated from environment: FITBIT_CLIENT_ID
client_secret = ""              # populated from environment: FITBIT_CLIENT_SECRET (omit for PKCE)
redirect_uri = ""               # e.g. "https://butler.tailnet.ts.net/hw/callback"
scopes = ["activity", "heartrate", "sleep", "oxygen_saturation",
          "respiratory_rate", "temperature", "cardio_fitness", "settings"]

[modules.health_wearable.apple_health]
webhook_secret = ""             # HMAC secret for incoming webhook verification
webhook_path = "/hw/apple_health/ingest"
```

---

## 12. Open Questions for Implementation

1. **OAuth redirect URI:** The Fitbit authorization redirect must resolve to the
   butler. Should the butler's FastAPI layer expose a `/hw/callback` route on
   the tailnet FQDN? This requires Tailscale HTTPS certificates to be configured.
   What is the secure default if Tailscale certs are not available?

2. **Initial pairing UX:** How does the user initiate the Fitbit OAuth pairing
   flow? Via an MCP tool call that returns a URL, or via a dashboard web page?
   The LLM CLI interaction model prefers the MCP tool approach, but the OAuth
   redirect requires a browser.

3. **Intraday data decision:** Intraday heart rate at 1-minute resolution for
   a full day is 1,440 data points. Over 90 days of retention, that is 129,600
   rows in `hw_heart_rate_intraday`. Is this acceptable volume for a single-user
   butler? PostgreSQL handles this comfortably, but storage and query performance
   should be considered.

4. **Multi-platform deduplication:** If a user has both Fitbit and Apple Watch,
   some metrics (steps, HR) may overlap in time and value. Should the butler
   prefer one source over another for aggregated queries, or store both sources
   separately and leave reconciliation to the LLM?

5. **Apple Health companion app:** Should the butler provide documentation for
   configuring Health Auto Export (third-party iOS app) as the push bridge, or
   should a dedicated iOS shortcut be documented? The former requires no custom
   development; the latter gives more control.

6. **Health Connect companion app:** Is building a dedicated Android companion
   app in scope for this project? If not, Health Connect integration should be
   explicitly marked as a future milestone.

7. **Fitbit account transition:** Fitbit accounts require a Google Account as of
   2023. Users who signed up before 2023 will eventually need to migrate. Does
   this affect the OAuth token structure or developer console access?

8. **Rate limit sharing:** The 150 req/hr limit is per user (i.e., per authorized
   OAuth token). If the butler syncs data for multiple users in a future
   multi-user scenario, each user has their own 150/hr budget. Document this
   explicitly in the implementation.

9. **Webhook security for Apple Health push:** Health Auto Export can be
   configured to include a shared secret in HTTP headers. The butler's
   webhook receiver must validate this signature before accepting data.

10. **Readiness / wellness score computation:** Fitbit provides raw metrics but
    not a composite "readiness score" equivalent to what Oura or Whoop provide.
    Should the butler module compute a simple readiness proxy (e.g., weighted
    average of resting HR deviation, HRV, sleep duration) from the raw Fitbit
    data, or leave interpretation entirely to the LLM?

---

## 13. Implementation Checklist (for future ticket)

When the implementation ticket is created:

1. Register a Fitbit application at `dev.fitbit.com/apps/oauthinteractivetutorial`
   as type `Personal` with requested scopes.
2. Create `src/butlers/modules/health_wearable.py` implementing
   `HealthWearableModule(Module)`.
3. Implement PKCE OAuth flow with `httpx` for Fitbit token acquisition.
4. Write Alembic migration `hw_001_create_health_wearable_tables.py`
   with all tables from section 6.2.
5. Implement Fitbit polling client: token refresh, endpoint wrappers, rate
   limit tracking via butler state store.
6. Implement normalisation layer: Fitbit response → unified schema insert.
7. Register cron task `hw_daily_sync` in `on_startup()`.
8. Expose MCP tools: `bot_hw_get_daily_summary`, `bot_hw_get_sleep_detail`,
   `bot_hw_get_hrv_trend`, `bot_hw_sync_now`, `user_hw_connect_fitbit`.
9. Add `[modules.health_wearable]` config section to butler TOML schema.
10. Add FastAPI route `/hw/callback` for OAuth redirect handling.
11. Add FastAPI route `/hw/apple_health/ingest` for Apple Health webhook (future).
12. Write unit tests: Fitbit client mocking, normalisation, rate limit logic.
13. Add `FITBIT_CLIENT_ID` / `FITBIT_CLIENT_SECRET` to `.env.example`.
14. Document companion app setup for Apple Health (Health Auto Export
    configuration guide).

---

## 14. References

**Fitbit Web API**
- [Fitbit Web API Reference](https://dev.fitbit.com/build/reference/web-api/)
- [Fitbit Web API Swagger UI](https://dev.fitbit.com/build/reference/web-api/explore/)
- [Fitbit Authorization Guide](https://dev.fitbit.com/build/reference/web-api/developer-guide/authorization/)
- [Fitbit Application Design Guide](https://dev.fitbit.com/build/reference/web-api/developer-guide/application-design/)
- [Fitbit OAuth2 Token Endpoint](https://dev.fitbit.com/build/reference/web-api/authorization/oauth2-token/)
- [Fitbit Refresh Token](https://dev.fitbit.com/build/reference/web-api/authorization/refresh-token/)
- [Fitbit Intraday Guide](https://dev.fitbit.com/build/reference/web-api/intraday/)
- [Fitbit Subscription Guide](https://dev.fitbit.com/build/reference/web-api/developer-guide/using-subscriptions/)
- [Fitbit Web API Data Dictionary v9 (Aug 2024)](https://assets.ctfassets.net/0ltkef2fmze1/45IN5bvBS827grKEsA8ZB0/648f3778acc936961f0572590c005ef0/Fitbit-Web-API-Data-Dictionary-Downloadable-Version-2.pdf)
- [Fitbit 7 New Data Types (Dec 2022)](https://dev.fitbit.com/blog/2022-12-06-announcing-new-data-types/)
- [python-fitbit (orcasgit, MIT)](https://github.com/orcasgit/python-fitbit)
- [fitbit-web-api PyPI (Swagger-generated)](https://pypi.org/project/fitbit-web-api/)
- [Fitbit Intraday Now Available to Personal Apps — Community](https://community.fitbit.com/t5/Web-API-Development/Intraday-data-now-immediately-available-to-personal-apps/td-p/1014524)

**Google Health Connect**
- [Health Connect Android Developer Docs](https://developer.android.com/health-and-fitness/health-connect)
- [Get Started with Health Connect](https://developer.android.com/health-and-fitness/health-connect/get-started)
- [Google Fit Migration Guide](https://developer.android.com/health-and-fitness/health-connect/migration/fit)
- [Google Fit Migration FAQ](https://developer.android.com/health-and-fitness/health-connect/migration/fit/faq)
- [Android Developers Blog: Evolving Health on Android (May 2024)](https://android-developers.googleblog.com/2024/05/evolving-health-on-android-migrating-from-google-fit-apis-to-android-health.html)
- [Android Developers Blog: IO 2024 Android Health Updates](https://android-developers.googleblog.com/2024/05/the-latest-updates-from-android-health-io-2024.html)
- [Google Fit Shutdown — Spike API](https://www.spikeapi.com/blog/google-fit-shutdown-what-developers-need-to-know-and-how-to-prepare)
- [Thryve: Health Connect Integration Guide](https://www.thryve.health/features/connections/health-connect)

**Apple Health**
- [How to Export Apple Health Data (2025)](https://applehealthdata.com/export-apple-health-data/)
- [Apple Health XML Parsing (GitHub Gist)](https://gist.github.com/hoffa/936db2bb85e134709cd263dd358ca309)
- [What You Can Do with Apple HealthKit Data — Momentum](https://www.themomentum.ai/blog/what-you-can-and-cant-do-with-apple-healthkit-data)
- [Health Auto Export App](https://www.healthyapps.dev/)
- [Health Auto Export — App Store](https://apps.apple.com/us/app/health-auto-export-json-csv/id1115567069)
- [Automatic Apple Health Export Configuration](https://www.healthyapps.dev/how-to-configure-automatic-apple-health-exports)
- [Apple Health MCP Server (Shortcuts integration)](https://applehealthdata.com/)

**Privacy, GDPR, and HIPAA**
- [HIPAA and GDPR Compliance for Health App Developers — LLIF](https://llif.org/2025/01/31/hipaa-gdpr-compliance-health-apps/)
- [Wearable Health Technology and HIPAA — TechTarget](https://www.techtarget.com/searchhealthit/feature/Wearable-health-technology-and-HIPAA-What-is-and-isnt-covered)
- [HIPAA Compliance in Wearable Devices — Paubox](https://www.paubox.com/blog/hipaa-compliance-in-wearable-devices)
- [FTC Health Breach Notification Rule 2024 — Coblentz Law](https://www.coblentzlaw.com/news/updates-to-u-s-health-data-privacy-and-wearable-tech/)
- [Fitbit Data Privacy Commitment — Google Support](https://support.google.com/fitbit/answer/14236817?hl=en-GB)
- [Privacy in Consumer Wearables — PMC Systematic Analysis](https://pmc.ncbi.nlm.nih.gov/articles/PMC12167361/)

**Unified Wearable Data Platforms (Reference)**
- [Open Wearables — Self-Hosted Unified API (MIT)](https://github.com/the-momentum/open-wearables)
- [Open Wearables Documentation](https://docs.openwearables.io/)
- [How to Integrate Health Data from Wearables — LLIF](https://llif.org/2025/04/28/how-to-integrate-health-data-from-wearables-apple-health-fitbit-google-fit/)
- [Fitbit Web API Python Sync 2026 — Johal.in](https://johal.in/fitbit-web-api-python-wearable-health-metrics-sync-2026/)
- [Terra API — Unified Fitness/Health API](https://tryterra.co/)
