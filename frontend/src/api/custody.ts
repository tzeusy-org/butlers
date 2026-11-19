/**
 * Owner endpoint custody (bu-s11n0s.1 S5g): the active holds, release and replace, and one
 * episode's case with the evidence summaries its contributors appended.
 *
 * Holds carry a target kind only, never an identifier or a hint. Release and replace post the
 * generation the owner saw plus a command id the page minted; the server rechecks both.
 */

import { apiFetch } from "@/api/client.ts";

export interface CustodyHold {
  hold_id: string;
  target_kind: string;
  generation: number;
  episode_id: string;
  reason: string;
  held_from: string;
}

export interface CaseEvidence {
  contributor: string;
  kind: string;
  contributed_at: string;
  summary: Record<
    string,
    string | number | boolean | null | { total: number | null }
  >;
}

export interface CustodyCase {
  state: string;
  outcome: string | null;
  opened_at: string;
  closed_at: string | null;
  evidence: CaseEvidence[];
  truncated: boolean;
}

interface Envelope<T> {
  data: T;
}

export async function listCustodyHolds(): Promise<CustodyHold[]> {
  const response =
    await apiFetch<Envelope<{ holds: CustodyHold[] }>>("/custody/holds");
  return response.data.holds;
}

export async function disposeHold(
  action: "release" | "replace",
  hold: Pick<CustodyHold, "hold_id" | "generation">,
  commandId: string,
): Promise<{ outcome: string }> {
  const response = await apiFetch<Envelope<{ outcome: string }>>(
    `/custody/holds/${encodeURIComponent(hold.hold_id)}/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command_id: commandId,
        expected_generation: hold.generation,
      }),
    },
  );
  return response.data;
}

export async function getCustodyCase(episodeId: string): Promise<CustodyCase> {
  const response = await apiFetch<Envelope<CustodyCase>>(
    `/custody/episodes/${encodeURIComponent(episodeId)}/case`,
  );
  return response.data;
}

export interface DeviceFix {
  status: "known" | "unknown";
  reason?: string;
  fix_at?: string;
  age_seconds?: number;
  place?: string | null;
  stale_fix_at?: string;
}

export interface CustodyDevice {
  bound: boolean;
  endpoint_identity?: string;
  last_fix: DeviceFix | null;
}

export async function getCustodyDevice(
  episodeId: string,
): Promise<CustodyDevice> {
  const response = await apiFetch<Envelope<CustodyDevice>>(
    `/custody/episodes/${encodeURIComponent(episodeId)}/device`,
  );
  return response.data;
}

export async function listCustodyDevices(episodeId: string): Promise<string[]> {
  const response = await apiFetch<
    Envelope<{ devices: { endpoint_identity: string }[] }>
  >(`/custody/episodes/${encodeURIComponent(episodeId)}/device/candidates`);
  return response.data.devices.map((device) => device.endpoint_identity);
}

export async function bindCustodyDevice(
  episodeId: string,
  endpointIdentity: string,
): Promise<void> {
  await apiFetch<Envelope<{ bound: boolean }>>(
    `/custody/episodes/${encodeURIComponent(episodeId)}/device`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint_identity: endpointIdentity }),
    },
  );
}
