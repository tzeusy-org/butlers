// ---------------------------------------------------------------------------
// ProviderConfigDrawer — reusable provider configuration drawer framework
// plus three concrete provider drawers: HomeAssistant, OwnTracks, Steam.
//
// Spec: butler-secrets §per-provider oddities
//
// Design-language rules (binding):
//   - No cards; hairlines only.
//   - Commit-pill actions (PillBtn variant="commit").
//   - Reveal demoted to tweak (pill, not commit).
//   - Status NEVER a word — dot or sliver only.
//   - Match established passport panel/commit-pill/drawer patterns.
//
// Provider endpoints used:
//   Home Assistant: POST /api/settings/home-assistant, DELETE same
//                   (useConfigureHomeAssistant, useDeleteHomeAssistantConfig,
//                    useHomeAssistantStatus)
//   OwnTracks:      POST /api/connectors/owntracks/token/generate
//                   (useOwnTracksGenerateToken, useOwnTracksStatus, useOwnTracksConfig)
//   Steam:          POST /api/steam/accounts, DELETE /api/steam/accounts/:id
//                   (useSteamConnect, useSteamDisconnect, useSteamAccounts)
//
// bu-ayp6v.8
// ---------------------------------------------------------------------------

import * as React from "react";

import { Mono, PillBtn, Eyebrow } from "./atoms.tsx";
import {
  useHomeAssistantStatus,
  useConfigureHomeAssistant,
  useDeleteHomeAssistantConfig,
} from "@/hooks/use-home-assistant.ts";
import {
  useOwnTracksStatus,
  useOwnTracksConfig,
  useOwnTracksGenerateToken,
} from "@/hooks/use-owntracks.ts";
import {
  useSteamAccounts,
  useSteamConnect,
  useSteamDisconnect,
} from "@/hooks/use-steam.ts";
import type { SteamConnectRequest } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// ProviderConfigDrawer — generic drawer shell
// ---------------------------------------------------------------------------

/**
 * ProviderConfigDrawer wraps provider-specific content with a consistent
 * heading + dismiss affordance that matches the passport's inline panel style.
 *
 * Usage:
 *   <ProviderConfigDrawer provider="homeassistant" label="Home Assistant" onClose={fn}>
 *     <HomeAssistantDrawerContent />
 *   </ProviderConfigDrawer>
 *
 * bu-ayp6v.9 reuse: pass a different `provider` slug and `children` to get the
 * same frame for Spotify (OAuth) or WhatsApp — those concrete implementations
 * live in .9 alongside the shell they wrap.
 *
 * When `inline` is true (embedded in PageUser), the dismiss button is omitted —
 * the content is always visible and no close affordance is needed.
 */
export function ProviderConfigDrawer({
  provider,
  label,
  onClose,
  inline = false,
  children,
}: {
  /** Provider slug used as data attribute for testability. */
  provider: string;
  /** Human-readable label shown in the heading. */
  label: string;
  /** Called when the user dismisses the drawer. Pass a no-op when inline=true. */
  onClose: () => void;
  /**
   * When true, renders without padding/heading (embedded inside PageUser's own
   * layout). When false (default), renders standalone with heading + dismiss.
   */
  inline?: boolean;
  children: React.ReactNode;
}) {
  if (inline) {
    return (
      <div data-provider-config-drawer={provider} data-provider-drawer-inline="true">
        {children}
      </div>
    );
  }

  return (
    <div
      className="flex flex-col gap-4.5 p-7"
      data-provider-config-drawer={provider}
    >
      {/* Heading */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <Eyebrow>configure provider</Eyebrow>
          <h1
            className="m-0 mt-2"
            style={{
              fontFamily: "var(--font-sans, 'Inter Tight', sans-serif)",
              fontSize: 28,
              fontWeight: 500,
              letterSpacing: "-0.025em",
              lineHeight: 1.08,
              color: "var(--fg)",
            }}
          >
            {label}
          </h1>
        </div>
        <PillBtn onClick={onClose}>dismiss</PillBtn>
      </div>

      {/* Hairline separator */}
      <div style={{ borderTop: "1px solid var(--border)" }} />

      {/* Provider-specific content */}
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Home Assistant drawer
// ---------------------------------------------------------------------------

/**
 * HomeAssistantDrawerContent — URL + long-lived token configure / status / disconnect.
 *
 * Endpoints: POST /api/settings/home-assistant (configure),
 *            DELETE /api/settings/home-assistant (disconnect).
 * Hooks: useHomeAssistantStatus, useConfigureHomeAssistant, useDeleteHomeAssistantConfig.
 */
export function HomeAssistantDrawerContent() {
  const statusQuery = useHomeAssistantStatus();
  const configureMutation = useConfigureHomeAssistant();
  const deleteMutation = useDeleteHomeAssistantConfig();

  const [url, setUrl] = React.useState("");
  const [token, setToken] = React.useState("");
  const [configureOpen, setConfigureOpen] = React.useState(false);
  const [disconnectOpen, setDisconnectOpen] = React.useState(false);

  const status = statusQuery.data;
  const isConnected = status?.state === "connected";
  const isConfigured = status?.url_configured || status?.token_configured;

  function handleConfigureOpen() {
    setConfigureOpen(true);
    setDisconnectOpen(false);
    configureMutation.reset();
    setUrl("");
    setToken("");
  }

  function handleConfigureCancel() {
    setConfigureOpen(false);
    setUrl("");
    setToken("");
    configureMutation.reset();
  }

  function handleConfigureSubmit() {
    if (!url.trim() || !token.trim() || configureMutation.isPending) return;
    configureMutation.mutate(
      { url: url.trim(), token: token.trim() },
      {
        onSuccess: () => {
          setConfigureOpen(false);
          setUrl("");
          setToken("");
        },
      },
    );
  }

  function handleDisconnectConfirm() {
    if (deleteMutation.isPending) return;
    deleteMutation.mutate();
  }

  function handleDisconnectCancel() {
    setDisconnectOpen(false);
    deleteMutation.reset();
  }

  if (statusQuery.isLoading) {
    return (
      <div data-ha-drawer-content="true">
        <Mono size={11} color="var(--dim)">loading…</Mono>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-ha-drawer-content="true">
      {/* Status row — dot only, never a word in the main flow */}
      <div className="flex items-center gap-2.5">
        <span
          className="inline-block shrink-0 rounded-full"
          style={{
            width: 6,
            height: 6,
            backgroundColor: isConnected
              ? "var(--green)"
              : isConfigured
                ? "var(--amber)"
                : "var(--dim)",
          }}
          aria-label={isConnected ? "connected" : isConfigured ? "configured" : "not configured"}
          data-ha-status-dot="true"
        />
        {status?.masked_url && (
          <Mono size={11} color="var(--mfg)">{status.masked_url}</Mono>
        )}
      </div>

      {/* KV band */}
      <div
        className="grid gap-5 py-3"
        style={{
          borderTop: "1px solid var(--border)",
          borderBottom: "1px solid var(--border)",
          gridTemplateColumns: "130px 130px",
        }}
      >
        <div>
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">url</Mono>
          <Mono size={11} className="mt-1 block">{status?.url_configured ? "configured" : "—"}</Mono>
        </div>
        <div>
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">token</Mono>
          <Mono size={11} className="mt-1 block">{status?.token_configured ? "configured" : "—"}</Mono>
        </div>
      </div>

      {/* Configure inline panel */}
      {configureOpen && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-ha-configure-panel="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            home assistant · url + token
          </Mono>

          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">url</Mono>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="http://homeassistant.local:8123"
              className="font-mono text-[11px] p-2 outline-none w-full"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
              data-ha-url-input="true"
            />
          </div>

          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">long-lived token</Mono>
            <textarea
              rows={3}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="paste long-lived access token"
              className="font-mono text-[11px] p-2 resize-none outline-none w-full"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
              data-ha-token-input="true"
            />
          </div>

          {configureMutation.error && (
            <Mono size={11} color="var(--red)">
              {configureMutation.error instanceof Error
                ? configureMutation.error.message
                : "Configure failed."}
            </Mono>
          )}

          {configureMutation.data && (
            <Mono size={11} color={configureMutation.data.success ? "var(--green)" : "var(--red)"}>
              {configureMutation.data.message}
            </Mono>
          )}

          <div className="flex gap-2">
            <PillBtn
              variant="commit"
              onClick={handleConfigureSubmit}
              disabled={!url.trim() || !token.trim() || configureMutation.isPending}
            >
              {configureMutation.isPending ? "saving…" : "save"}
            </PillBtn>
            <PillBtn onClick={handleConfigureCancel} disabled={configureMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {/* Disconnect inline confirm */}
      {disconnectOpen && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--red)", background: "var(--bg-elev)" }}
          data-ha-disconnect-confirm="true"
        >
          <Mono size={11} color="var(--red)">
            Remove Home Assistant credentials? This cannot be undone.
          </Mono>
          {deleteMutation.error && (
            <Mono size={11} color="var(--red)">
              {deleteMutation.error instanceof Error
                ? deleteMutation.error.message
                : "Disconnect failed."}
            </Mono>
          )}
          <div className="flex gap-2">
            <PillBtn
              variant="danger"
              onClick={handleDisconnectConfirm}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? "removing…" : "yes, disconnect"}
            </PillBtn>
            <PillBtn onClick={handleDisconnectCancel} disabled={deleteMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {statusQuery.error && (
        <Mono size={11} color="var(--red)">
          {statusQuery.error instanceof Error
            ? statusQuery.error.message
            : "Status unavailable."}
        </Mono>
      )}

      {/* Footer */}
      <div
        className="flex justify-between items-center pt-3.5 flex-wrap gap-2"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        <div className="flex gap-2 flex-wrap">
          <PillBtn
            variant={!isConfigured ? "commit" : "pill"}
            onClick={handleConfigureOpen}
            disabled={configureOpen}
          >
            {isConfigured ? "reconfigure" : "configure"}
          </PillBtn>
        </div>
        {isConfigured && (
          <PillBtn
            variant="danger"
            onClick={() => { setDisconnectOpen(true); setConfigureOpen(false); }}
            disabled={disconnectOpen}
          >
            disconnect
          </PillBtn>
        )}
      </div>
    </div>
  );
}

/**
 * HomeAssistantDrawer — full drawer: shell + content.
 * Opened from PassportAddPanel (connect flow) or PageUser (already connected).
 *
 * Pass `inline` when embedding inside PageUser's own layout — omits the
 * standalone heading and dismiss button.
 */
export function HomeAssistantDrawer({
  onClose,
  inline = false,
}: {
  onClose: () => void;
  inline?: boolean;
}) {
  return (
    <ProviderConfigDrawer provider="homeassistant" label="Home Assistant" onClose={onClose} inline={inline}>
      <HomeAssistantDrawerContent />
    </ProviderConfigDrawer>
  );
}

// ---------------------------------------------------------------------------
// OwnTracks drawer
// ---------------------------------------------------------------------------

/**
 * OwnTracksDrawerContent — generate/regenerate webhook token + display URL.
 *
 * Endpoints: POST /api/connectors/owntracks/token/generate (generate/regenerate).
 * Hooks: useOwnTracksGenerateToken, useOwnTracksStatus, useOwnTracksConfig.
 *
 * The generated token is shown copy-once (like CLI rotate). After dismiss it's gone.
 */
export function OwnTracksDrawerContent() {
  const statusQuery = useOwnTracksStatus();
  const configQuery = useOwnTracksConfig();
  const generateMutation = useOwnTracksGenerateToken();

  const [generatedToken, setGeneratedToken] = React.useState<string | null>(null);
  const [confirmRegenerate, setConfirmRegenerate] = React.useState(false);

  const status = statusQuery.data;
  const config = configQuery.data;
  const isActive = status?.state === "active";
  const tokenConfigured = status?.token_configured ?? false;

  function handleGenerate() {
    if (generateMutation.isPending) return;
    setConfirmRegenerate(false);
    setGeneratedToken(null);
    generateMutation.mutate(undefined, {
      onSuccess: (data) => {
        setGeneratedToken(data.token);
      },
    });
  }

  function handleRegenerate() {
    setConfirmRegenerate(true);
  }

  function handleRegenerateConfirm() {
    setConfirmRegenerate(false);
    handleGenerate();
  }

  function handleRegenerateCancel() {
    setConfirmRegenerate(false);
    generateMutation.reset();
  }

  if (statusQuery.isLoading || configQuery.isLoading) {
    return (
      <div data-owntracks-drawer-content="true">
        <Mono size={11} color="var(--dim)">loading…</Mono>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-owntracks-drawer-content="true">
      {/* Status dot */}
      <div className="flex items-center gap-2.5">
        <span
          className="inline-block shrink-0 rounded-full"
          style={{
            width: 6,
            height: 6,
            backgroundColor: isActive
              ? "var(--green)"
              : tokenConfigured
                ? "var(--amber)"
                : "var(--dim)",
          }}
          aria-label={isActive ? "active" : tokenConfigured ? "idle" : "not configured"}
          data-owntracks-status-dot="true"
        />
        {status && (
          <Mono size={11} color="var(--mfg)">
            {status.events_today} event{status.events_today === 1 ? "" : "s"} today
          </Mono>
        )}
      </div>

      {/* KV band — webhook URL */}
      <div
        className="flex flex-col gap-3 py-3"
        style={{
          borderTop: "1px solid var(--border)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div>
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">webhook url</Mono>
          <div className="flex items-center gap-2 mt-1">
            <span
              className="font-mono tabular-nums flex-1 min-w-0 break-all"
              style={{ fontSize: 11, color: "var(--fg)" }}
              data-owntracks-webhook-url="true"
            >
              {config?.webhook_url ?? "—"}
            </span>
            {config?.webhook_url && (
              <PillBtn
                onClick={() => navigator.clipboard?.writeText(config.webhook_url)}
              >
                copy
              </PillBtn>
            )}
          </div>
        </div>
        <div>
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">token</Mono>
          <Mono size={11} className="mt-1 block">{tokenConfigured ? "configured" : "not set"}</Mono>
        </div>
        {status?.last_event_at && (
          <div>
            <Mono size={9} upper tracking="0.14em" color="var(--dim)">last event</Mono>
            <Mono size={11} className="mt-1 block">{status.last_event_at}</Mono>
          </div>
        )}
      </div>

      {/* Generated token copy-once panel */}
      {generatedToken !== null && (
        <div
          className="flex flex-col gap-2 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-owntracks-token-panel="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            {tokenConfigured ? "new token — copy now, replaces previous" : "token — copy now, won't be shown again"}
          </Mono>
          <Mono size={12} className="break-all" data-owntracks-token-value="true">{generatedToken}</Mono>
          <div className="flex gap-2">
            <PillBtn onClick={() => navigator.clipboard?.writeText(generatedToken)}>
              copy
            </PillBtn>
            <PillBtn onClick={() => setGeneratedToken(null)}>
              dismiss
            </PillBtn>
          </div>
        </div>
      )}

      {/* Regenerate confirm panel */}
      {confirmRegenerate && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--amber)", background: "var(--bg-elev)" }}
          data-owntracks-regenerate-confirm="true"
        >
          <Mono size={11} color="var(--amber)">
            Regenerate token? The OwnTracks app will need to be reconfigured with the new token.
          </Mono>
          <div className="flex gap-2">
            <PillBtn
              variant="commit"
              onClick={handleRegenerateConfirm}
              disabled={generateMutation.isPending}
            >
              {generateMutation.isPending ? "generating…" : "yes, regenerate"}
            </PillBtn>
            <PillBtn onClick={handleRegenerateCancel} disabled={generateMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {generateMutation.error && (
        <Mono size={11} color="var(--red)">
          {generateMutation.error instanceof Error
            ? generateMutation.error.message
            : "Token generation failed."}
        </Mono>
      )}

      {/* Footer */}
      <div
        className="flex gap-2 pt-3.5 flex-wrap"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        {!tokenConfigured ? (
          <PillBtn
            variant="commit"
            onClick={handleGenerate}
            disabled={generateMutation.isPending || generatedToken !== null}
          >
            {generateMutation.isPending ? "generating…" : "generate token"}
          </PillBtn>
        ) : (
          <PillBtn
            onClick={handleRegenerate}
            disabled={confirmRegenerate || generateMutation.isPending}
          >
            regenerate token
          </PillBtn>
        )}
      </div>
    </div>
  );
}

/**
 * OwnTracksDrawer — full drawer: shell + content.
 *
 * Pass `inline` when embedding inside PageUser's own layout.
 */
export function OwnTracksDrawer({
  onClose,
  inline = false,
}: {
  onClose: () => void;
  inline?: boolean;
}) {
  return (
    <ProviderConfigDrawer provider="owntracks" label="OwnTracks" onClose={onClose} inline={inline}>
      <OwnTracksDrawerContent />
    </ProviderConfigDrawer>
  );
}

// ---------------------------------------------------------------------------
// Steam drawer
// ---------------------------------------------------------------------------

/**
 * SteamDrawerContent — API key + SteamID64 connect, list accounts, disconnect.
 *
 * Endpoints: POST /api/steam/accounts (connect), DELETE /api/steam/accounts/:id (disconnect).
 * Hooks: useSteamAccounts, useSteamConnect, useSteamDisconnect.
 */
export function SteamDrawerContent() {
  const accountsQuery = useSteamAccounts();
  const connectMutation = useSteamConnect();
  const disconnectMutation = useSteamDisconnect();

  const [connectOpen, setConnectOpen] = React.useState(false);
  const [apiKey, setApiKey] = React.useState("");
  const [steamId, setSteamId] = React.useState("");
  const [disconnectTarget, setDisconnectTarget] = React.useState<string | null>(null);

  const accounts = accountsQuery.data?.accounts ?? [];

  function handleConnectOpen() {
    setConnectOpen(true);
    setApiKey("");
    setSteamId("");
    connectMutation.reset();
  }

  function handleConnectCancel() {
    setConnectOpen(false);
    setApiKey("");
    setSteamId("");
    connectMutation.reset();
  }

  function handleConnectSubmit() {
    if (!apiKey.trim() || !steamId.trim() || connectMutation.isPending) return;
    const data: SteamConnectRequest = {
      api_key: apiKey.trim(),
      steam_id: steamId.trim(),
    };
    connectMutation.mutate(data, {
      onSuccess: () => {
        setConnectOpen(false);
        setApiKey("");
        setSteamId("");
      },
    });
  }

  function handleDisconnectConfirm(accountId: string) {
    if (disconnectMutation.isPending) return;
    disconnectMutation.mutate(accountId, {
      onSuccess: () => {
        setDisconnectTarget(null);
      },
    });
  }

  function handleDisconnectCancel() {
    setDisconnectTarget(null);
    disconnectMutation.reset();
  }

  if (accountsQuery.isLoading) {
    return (
      <div data-steam-drawer-content="true">
        <Mono size={11} color="var(--dim)">loading…</Mono>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-steam-drawer-content="true">
      {/* Account list */}
      <div>
        <div className="flex items-center justify-between gap-3 mb-1">
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">steam accounts</Mono>
          <Mono size={9} color="var(--dim)">{accounts.length} connected</Mono>
        </div>

        {accounts.length === 0 ? (
          <div
            className="pt-2"
            style={{ borderTop: "1px solid var(--border)" }}
          >
            <Mono size={11} color="var(--dim)">no accounts connected</Mono>
          </div>
        ) : (
          accounts.map((account) => (
            <div
              key={account.id}
              className="flex flex-col gap-2 py-2.5"
              style={{ borderTop: "1px solid var(--border)" }}
              data-steam-account-row={account.id}
            >
              {/* Account row */}
              <div className="flex items-center gap-2.5 min-w-0">
                <span
                  className="inline-block shrink-0 rounded-full"
                  style={{
                    width: 6,
                    height: 6,
                    backgroundColor:
                      account.status === "active"
                        ? "var(--green)"
                        : account.status === "suspended"
                          ? "var(--amber)"
                          : "var(--red)",
                  }}
                  aria-label={account.status}
                  data-steam-account-dot={account.status}
                />
                <Mono size={12} className="flex-1 min-w-0 truncate">
                  {account.display_name ?? account.steam_id}
                </Mono>
                <Mono size={9} color="var(--dim)">{account.steam_id}</Mono>
              </div>

              {/* Actions */}
              <div className="flex gap-2 flex-wrap pl-[14px]">
                <PillBtn
                  variant="danger"
                  onClick={() => {
                    setDisconnectTarget(account.id);
                    setConnectOpen(false);
                    disconnectMutation.reset();
                  }}
                  disabled={disconnectTarget === account.id}
                >
                  disconnect
                </PillBtn>
              </div>

              {/* Disconnect confirm for this account */}
              {disconnectTarget === account.id && (
                <div
                  className="flex flex-col gap-2.5 p-3.5 ml-[14px]"
                  style={{ border: "1px solid var(--red)", background: "var(--bg-elev)" }}
                  data-steam-disconnect-confirm={account.id}
                >
                  <Mono size={11} color="var(--red)">
                    Disconnect {account.display_name ?? account.steam_id}? Removes stored API key and SteamID.
                  </Mono>
                  {disconnectMutation.error && (
                    <Mono size={11} color="var(--red)">
                      {disconnectMutation.error instanceof Error
                        ? disconnectMutation.error.message
                        : "Disconnect failed."}
                    </Mono>
                  )}
                  <div className="flex gap-2">
                    <PillBtn
                      variant="danger"
                      onClick={() => handleDisconnectConfirm(account.id)}
                      disabled={disconnectMutation.isPending}
                    >
                      {disconnectMutation.isPending ? "disconnecting…" : "yes, disconnect"}
                    </PillBtn>
                    <PillBtn
                      onClick={handleDisconnectCancel}
                      disabled={disconnectMutation.isPending}
                    >
                      cancel
                    </PillBtn>
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Connect inline panel */}
      {connectOpen && (
        <div
          className="flex flex-col gap-3 p-3.5"
          style={{ border: "1px solid var(--border-soft)", background: "var(--bg-elev)" }}
          data-steam-connect-panel="true"
        >
          <Mono size={9} upper tracking="0.14em" color="var(--dim)">
            connect steam account
          </Mono>

          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">api key</Mono>
            <input
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Steam Web API key"
              className="font-mono text-[11px] p-2 outline-none w-full"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
              data-steam-api-key-input="true"
            />
          </div>

          <div className="flex flex-col gap-1">
            <Mono size={9} upper tracking="0.12em" color="var(--dim)">steamid64</Mono>
            <input
              type="text"
              value={steamId}
              onChange={(e) => setSteamId(e.target.value)}
              placeholder="76561198000000000"
              className="font-mono text-[11px] p-2 outline-none w-full"
              style={{
                border: "1px solid var(--border-strong)",
                background: "var(--bg)",
                color: "var(--fg)",
                borderRadius: 3,
              }}
              data-steam-id-input="true"
            />
            <Mono size={9} color="var(--dim)">
              17-digit SteamID64 · find at steamid.io
            </Mono>
          </div>

          {connectMutation.error && (
            <Mono size={11} color="var(--red)">
              {connectMutation.error instanceof Error
                ? connectMutation.error.message
                : "Connect failed."}
            </Mono>
          )}

          <div className="flex gap-2">
            <PillBtn
              variant="commit"
              onClick={handleConnectSubmit}
              disabled={!apiKey.trim() || !steamId.trim() || connectMutation.isPending}
            >
              {connectMutation.isPending ? "connecting…" : "connect"}
            </PillBtn>
            <PillBtn onClick={handleConnectCancel} disabled={connectMutation.isPending}>
              cancel
            </PillBtn>
          </div>
        </div>
      )}

      {accountsQuery.error && (
        <Mono size={11} color="var(--red)">
          {accountsQuery.error instanceof Error
            ? accountsQuery.error.message
            : "Could not load accounts."}
        </Mono>
      )}

      {/* Footer */}
      <div
        className="flex gap-2 pt-3.5"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        <PillBtn
          variant="commit"
          onClick={handleConnectOpen}
          disabled={connectOpen}
        >
          connect account
        </PillBtn>
      </div>
    </div>
  );
}

/**
 * SteamDrawer — full drawer: shell + content.
 *
 * Pass `inline` when embedding inside PageUser's own layout.
 */
export function SteamDrawer({
  onClose,
  inline = false,
}: {
  onClose: () => void;
  inline?: boolean;
}) {
  return (
    <ProviderConfigDrawer provider="steam" label="Steam" onClose={onClose} inline={inline}>
      <SteamDrawerContent />
    </ProviderConfigDrawer>
  );
}
