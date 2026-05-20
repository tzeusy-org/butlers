/**
 * Shared constants for batch connector settings.
 *
 * Extracted from BatchSettingsCard so consumers (ConnectorDetailPage,
 * tests) can import without pulling in React or the component tree
 * (resolves react-refresh/only-export-components).
 */

/** Connector types that support batch settings (flush_interval_s). */
export const BATCH_CONNECTOR_TYPES = new Set([
  "telegram_user_client",
  "whatsapp_user_client",
]);
