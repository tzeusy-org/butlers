// ---------------------------------------------------------------------------
// TweaksPanel — 4-toggle panel for /secrets [bu-qu8v8]
//
// Persists state to localStorage via `secrets.tweaks.*` keys.
// Spec: butler-secrets §Tweaks-Panel State Persistence
//
// Toggles:
//   reveal-mode:    eye | hover | never  (default: eye)
//   default-sort:   severity | recency | alpha  (default: severity)
//   show-verify-cmd: off/on  (default: off)
//   voice-paragraph: on/off  (default: on)
// ---------------------------------------------------------------------------

import * as React from "react";

import { cn } from "@/lib/utils";
import { readBooleanSetting, writeBooleanSetting } from "@/lib/local-settings";
import type { SecretsTweaks, RevealMode, SpineSortMode } from "./types.ts";
import { TWEAKS_KEYS, TWEAKS_DEFAULTS } from "./constants.ts";
import { Mono } from "./atoms.tsx";

// ── localStorage helpers ─────────────────────────────────────────────────────

function readStringSetting(key: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function writeStringSetting(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Ignore write failures.
  }
}

// ── Hook: useTweaks ──────────────────────────────────────────────────────────

/** Read and persist tweak state via localStorage. */
// eslint-disable-next-line react-refresh/only-export-components
export function useTweaks(): [SecretsTweaks, <K extends keyof SecretsTweaks>(key: K, value: SecretsTweaks[K]) => void] {
  const [tweaks, setTweaksState] = React.useState<SecretsTweaks>(() => ({
    revealMode:    readStringSetting(TWEAKS_KEYS.revealMode, TWEAKS_DEFAULTS.revealMode) as RevealMode,
    defaultSort:   readStringSetting(TWEAKS_KEYS.defaultSort, TWEAKS_DEFAULTS.defaultSort) as SpineSortMode,
    showVerifyCmd: readBooleanSetting(TWEAKS_KEYS.showVerifyCmd, TWEAKS_DEFAULTS.showVerifyCmd),
    voiceParagraph:readBooleanSetting(TWEAKS_KEYS.voiceParagraph, TWEAKS_DEFAULTS.voiceParagraph),
  }));

  function setTweak<K extends keyof SecretsTweaks>(key: K, value: SecretsTweaks[K]) {
    const storageKey = TWEAKS_KEYS[key];
    if (typeof value === "boolean") {
      writeBooleanSetting(storageKey, value);
    } else {
      writeStringSetting(storageKey, String(value));
    }
    setTweaksState((prev) => ({ ...prev, [key]: value }));
  }

  return [tweaks, setTweak];
}

// ── Components ─────────────────────────────────────────────────────────────

function TweakRadio<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Mono size={10} upper tracking="0.12em" color="var(--mfg)">
        {label}
      </Mono>
      <div className="flex items-center gap-1.5 flex-wrap">
        {options.map((opt, i) => (
          <React.Fragment key={opt.value}>
            {i > 0 && (
              <Mono size={9} color="var(--dim)">
                ·
              </Mono>
            )}
            <button
              type="button"
              onClick={() => onChange(opt.value)}
              className={cn(
                "bg-transparent border-none cursor-pointer p-0 font-mono text-[9.5px] uppercase tracking-[0.08em]",
                value === opt.value
                  ? "text-[var(--fg)] border-b border-[var(--fg)]"
                  : "text-[var(--dim)]",
              )}
              aria-pressed={value === opt.value}
              data-tweak-option={opt.value}
            >
              {opt.label}
            </button>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

function TweakToggle({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <Mono size={10} upper tracking="0.12em" color="var(--mfg)">
        {label}
      </Mono>
      <button
        type="button"
        role="switch"
        aria-checked={value}
        onClick={() => onChange(!value)}
        className={cn(
          "relative inline-flex items-center w-8 h-4 rounded-full border transition-colors cursor-pointer",
          value
            ? "bg-[var(--fg)] border-[var(--fg)]"
            : "bg-transparent border-[var(--border-strong)]",
        )}
        data-tweak-toggle={label}
      >
        <span
          className="inline-block w-2.5 h-2.5 rounded-full transition-transform"
          style={{
            transform: value ? "translateX(18px)" : "translateX(2px)",
            backgroundColor: value ? "var(--bg)" : "var(--dim)",
          }}
        />
      </button>
    </div>
  );
}

function TweakSection({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3">
      <Mono size={9} upper tracking="0.14em" color="var(--dim)">
        {label}
      </Mono>
      {children}
    </div>
  );
}

// ── TweaksPanel ──────────────────────────────────────────────────────────────

/** 4-toggle tweaks panel for /secrets. */
export function TweaksPanel({
  tweaks,
  onTweak,
  open,
  onOpenChange,
  className,
}: {
  tweaks: SecretsTweaks;
  onTweak: <K extends keyof SecretsTweaks>(key: K, value: SecretsTweaks[K]) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  className?: string;
}) {
  return (
    <div className={cn("relative", className)}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        aria-expanded={open}
        className="font-mono text-[10px] uppercase tracking-[0.14em] px-2.5 py-1 border border-[var(--border-strong)] rounded-sm bg-transparent text-[var(--mfg)] hover:text-[var(--fg)] transition-colors cursor-pointer"
        data-tweaks-trigger="true"
      >
        tweaks
        {open ? " ▲" : " ▾"}
      </button>

      {/* Panel */}
      {open && (
        <div
          className="absolute right-0 top-full mt-2 z-50 flex flex-col gap-4 p-4 border border-[var(--border)] rounded-sm"
          style={{
            background: "var(--bg-elev)",
            minWidth: 220,
          }}
          data-tweaks-panel="true"
        >
          <TweakSection label="Privacy">
            <TweakRadio<RevealMode>
              label="Reveal mode"
              value={tweaks.revealMode}
              options={[
                { value: "eye",   label: "eye"   },
                { value: "hover", label: "hover" },
                { value: "never", label: "never" },
              ]}
              onChange={(v) => onTweak("revealMode", v)}
            />
            <TweakToggle
              label="Show verify cmd"
              value={tweaks.showVerifyCmd}
              onChange={(v) => onTweak("showVerifyCmd", v)}
            />
          </TweakSection>

          <TweakSection label="Spine">
            <TweakRadio<SpineSortMode>
              label="Default sort"
              value={tweaks.defaultSort}
              options={[
                { value: "severity", label: "severity" },
                { value: "recency",  label: "recency"  },
                { value: "alpha",    label: "alpha"    },
              ]}
              onChange={(v) => onTweak("defaultSort", v)}
            />
          </TweakSection>

          <TweakSection label="Voice">
            <TweakToggle
              label="Voice paragraph"
              value={tweaks.voiceParagraph}
              onChange={(v) => onTweak("voiceParagraph", v)}
            />
          </TweakSection>
        </div>
      )}
    </div>
  );
}
