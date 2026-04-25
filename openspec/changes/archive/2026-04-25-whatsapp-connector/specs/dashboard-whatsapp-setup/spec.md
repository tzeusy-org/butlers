# Dashboard WhatsApp Setup

## Purpose

A dedicated section on the settings page at `/butlers/settings` for linking, monitoring, and managing the user's WhatsApp account. Modeled after the Google OAuth account management pattern: status card with health badge, account linking flow (QR pairing instead of OAuth redirect), session health monitoring, and disconnect capability.

## ADDED Requirements

### Requirement: WhatsApp Settings Section

The settings page SHALL include a WhatsApp section with connection status and account management.

#### Scenario: WhatsApp section on settings page

- **WHEN** the user navigates to `/butlers/settings`
- **THEN** a "WhatsApp" section SHALL be displayed alongside the existing Google OAuth section
- **AND** it SHALL show the current connection state with a color-coded health badge:
  - `connected` (green) — session active, bridge connected
  - `disconnected` (amber) — session exists but bridge not connected
  - `pair_required` (red) — no valid session, QR pairing needed
  - `not_configured` (outline) — no WhatsApp setup attempted

#### Scenario: Connected state display

- **WHEN** the WhatsApp account is connected
- **THEN** the settings section SHALL show:
  - Phone number (masked: `+1 *** *** 7890`)
  - Paired date
  - Last sync timestamp
  - Connection health badge
  - "Disconnect" button

#### Scenario: Not configured state display

- **WHEN** no WhatsApp account is linked
- **THEN** the settings section SHALL show a "Link WhatsApp Account" button
- **AND** a brief explanation: "Connect your WhatsApp to give butlers awareness of your WhatsApp conversations. Read-only — butlers will not send messages."

### Requirement: QR Pairing Flow

The dashboard provides a browser-based QR pairing flow, eliminating the need for CLI access.

#### Scenario: Initiate pairing

- **WHEN** the user clicks "Link WhatsApp Account"
- **THEN** a modal SHALL open showing a loading state
- **AND** the frontend SHALL call `POST /api/connectors/whatsapp/pair/start`
- **AND** the API SHALL return a QR code as a base64-encoded PNG data URI

#### Scenario: QR code display

- **WHEN** the QR data URI is received
- **THEN** the modal SHALL display the QR code at a scannable size (minimum 256x256 pixels)
- **AND** instructions: "Open WhatsApp on your phone → Settings → Linked Devices → Link a Device → Scan this QR code"

#### Scenario: QR code refresh

- **WHEN** the QR code expires before scanning (WhatsApp QR codes expire after ~60 seconds)
- **THEN** the frontend SHALL automatically request a new QR code from `POST /api/connectors/whatsapp/pair/start`
- **AND** the modal SHALL show a brief "Refreshing..." state before displaying the new QR

#### Scenario: Pairing completion detection

- **WHEN** the QR code is displayed
- **THEN** the frontend SHALL poll `GET /api/connectors/whatsapp/pair/poll` (every 2 seconds)
- **AND** when pairing succeeds, the API SHALL return `{"status": "paired", "phone": "+1234567890"}`
- **AND** the modal SHALL close with a success toast notification
- **AND** the settings section SHALL update to show the connected state

#### Scenario: Pairing timeout

- **WHEN** the user does not scan the QR code within 120 seconds
- **THEN** the modal SHALL show "Pairing timed out" with a "Try Again" button

#### Scenario: Pairing error

- **WHEN** the bridge fails to generate a QR code (e.g., bridge not running)
- **THEN** the modal SHALL show an error: "Could not connect to WhatsApp bridge. Ensure the connector service is running."

### Requirement: Session Health Monitoring

The settings page provides ongoing visibility into WhatsApp session health.

#### Scenario: Health badge polling

- **WHEN** the settings page is open and WhatsApp is configured
- **THEN** the frontend SHALL poll `GET /api/connectors/whatsapp/health` every 30 seconds
- **AND** the health badge color SHALL update based on the response state

#### Scenario: Session expired alert

- **WHEN** the bridge reports `state: "pair_required"` (session invalidated by phone logout or ban)
- **THEN** the health badge SHALL turn red
- **AND** a "Re-pair" button SHALL appear next to the badge
- **AND** clicking "Re-pair" SHALL initiate the QR pairing flow

#### Scenario: Bridge not running alert

- **WHEN** the health endpoint returns an error or the bridge is unreachable
- **THEN** the health badge SHALL show amber with tooltip: "WhatsApp bridge is not running"

### Requirement: Disconnect Flow

The user can disconnect their WhatsApp account from the dashboard.

#### Scenario: Disconnect account

- **WHEN** the user clicks "Disconnect"
- **THEN** a confirmation dialog SHALL appear: "Disconnect WhatsApp? You'll need to re-scan the QR code to reconnect."
- **AND** on confirmation, `POST /api/connectors/whatsapp/disconnect` SHALL be called
- **AND** the bridge SHALL gracefully disconnect and mark the session as inactive
- **AND** the settings section SHALL update to show `not_configured` state

### Requirement: Dashboard API Endpoints

REST API endpoints for WhatsApp account management, served by a FastAPI router.

#### Scenario: Status endpoint

- **WHEN** `GET /api/connectors/whatsapp/status` is called
- **THEN** it SHALL return JSON with:
  - `state`: one of `"connected"`, `"disconnected"`, `"pair_required"`, `"not_configured"`
  - `phone`: connected phone number (masked for display) or null
  - `paired_at`: ISO datetime or null
  - `last_sync_at`: ISO datetime or null
  - `bridge_running`: boolean

#### Scenario: Pair start endpoint

- **WHEN** `POST /api/connectors/whatsapp/pair/start` is called
- **THEN** it SHALL instruct the Go bridge to generate a new QR code
- **AND** return `{"qr_data_uri": "data:image/png;base64,...", "expires_at": "ISO datetime"}`
- **AND** return HTTP 503 if the bridge is not running

#### Scenario: Pair poll endpoint

- **WHEN** `GET /api/connectors/whatsapp/pair/poll` is called
- **THEN** it SHALL return `{"status": "waiting"}` if pairing is still in progress
- **AND** return `{"status": "paired", "phone": "+1234567890"}` when pairing completes
- **AND** return `{"status": "expired"}` if the QR code expired without being scanned

#### Scenario: Health endpoint

- **WHEN** `GET /api/connectors/whatsapp/health` is called
- **THEN** it SHALL proxy the bridge's `/status` endpoint and return the session health
- **AND** return `{"state": "not_configured", "bridge_running": false}` if no bridge is reachable

#### Scenario: Disconnect endpoint

- **WHEN** `POST /api/connectors/whatsapp/disconnect` is called
- **THEN** it SHALL instruct the bridge to disconnect gracefully
- **AND** mark the session as inactive in `whatsapp_sessions`
- **AND** return `{"success": true, "message": "WhatsApp disconnected"}`
