import type { OwnerStatus, SessionTuple } from "../../../src/api/owner-auth";
import type { ApiResponse } from "../../../src/api/types";
import type { PermissionsMatrix, WebhookRow, WebhookTestResult } from "../../../src/pages/SettingsPermissionsPage";
import type { SpendRule, BreakdownData } from "../../../src/pages/SpendPage";
import type { ForecastData } from "../../../src/lib/spend-forecast";
import type { BackendResponses } from "../../../.vite/e2e-backend-responses";
import type * as api from "../../../src/api/client";

/** The response is owned by the same endpoint function used by the UI. */
type WireResponses = BackendResponses & {
  ownerStatus: { data: OwnerStatus };
  ownerCsrf: { data: SessionTuple };
  getConnectorDetail: ApiResponse<api._ConnectorDetailPayload>;
  getConnectorStats: ApiResponse<api._BackendStatsRow[]>;
  getPermissions: ApiResponse<PermissionsMatrix>;
  getWebhooks: ApiResponse<WebhookRow[]>;
  testWebhook: ApiResponse<WebhookTestResult>;
  getSpendForecast: ApiResponse<ForecastData>;
  getSpendBreakdown: ApiResponse<BreakdownData>;
  getSpendRules: ApiResponse<SpendRule[]>;
};
type Endpoint = keyof WireResponses | Exclude<keyof typeof api, "apiFetch">;

export type ResponseOf<K extends Endpoint> = K extends keyof WireResponses
  ? WireResponses[K]
  : K extends keyof typeof api ? (typeof api)[K] extends (...args: never[]) => infer R ? Awaited<R> : never : never;

/** Check before serialization; JSON.stringify itself accepts untyped input. */
export function mockJson<K extends Endpoint>(
  endpoint: K,
  body: NoInfer<ResponseOf<K>>,
): string {
  void endpoint;
  return JSON.stringify(body);
}

/** Playwright's `json` option has the same checked boundary without encoding. */
export function mockResponse<K extends Endpoint>(
  endpoint: K,
  body: NoInfer<ResponseOf<K>>,
): ResponseOf<K> {
  void endpoint;
  return body;
}
