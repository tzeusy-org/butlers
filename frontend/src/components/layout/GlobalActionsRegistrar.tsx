/**
 * Registers the always-available command-menu Actions (bu-86c4c.7) — the
 * ones that aren't specific to any one page. "Run <butler>" runs the
 * butler's scheduled tick right now, same as the Force Run button on the
 * butler detail page (ButlerDetailActions.tsx), but reachable from anywhere
 * via the command menu instead of only from that one page.
 *
 * This is a plain consumer of the per-page command registration API
 * (src/lib/command-registry.tsx) — it demonstrates that "per-page" really
 * means "per mounted component," global or not. Mounted once in RootLayout,
 * inside CommandRegistryProvider.
 */

import { useMemo } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { triggerButler } from "@/api/index";
import { useButlers } from "@/hooks/use-butlers";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";

/**
 * Complexity tier used for the palette's quick-trigger action. Must be one of
 * the backend's valid tiers (reasoning/workhorse/cheap/specialty/local/legacy
 * -- see model_settings.py:_COMPLEXITY_TIERS); "workhorse" mirrors the
 * backend TriggerRequest default (matches ButlerDetailActions.tsx's
 * DEFAULT_COMPLEXITY, bu-86c4c.18).
 */
const DEFAULT_COMPLEXITY = "workhorse";

export function GlobalActionsRegistrar() {
  const { data: butlersResponse } = useButlers();
  const navigate = useNavigate();

  const commands = useMemo<PaletteCommand[]>(() => {
    const butlers = butlersResponse?.data ?? [];
    return butlers.map((b) => ({
      id: `trigger:${b.name}`,
      label: `Run ${b.name}`,
      keywords: ["trigger", "run", "force run", b.name],
      perform: async () => {
        try {
          const response = await triggerButler(
            b.name,
            "Run your scheduled tick now.",
            DEFAULT_COMPLEXITY,
          );
          toast.success(`Started ${b.name}`);
          if (response.session_id) navigate(`/sessions/${response.session_id}`);
        } catch {
          toast.error(`Failed to run ${b.name}`);
        }
      },
    }));
  }, [butlersResponse, navigate]);

  useRegisterCommands(commands);

  return null;
}
