/**
 * OwnerSetupBanner
 *
 * Shown at the top of the /contacts/ page when the owner contact has no
 * identifiers configured (no telegram handle, no email address). Prompts
 * the user to set up their identity.
 */

import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { useOwnerSetupStatus } from "@/hooks/use-contacts";

export function OwnerSetupBanner() {
  const { data: status, isLoading } = useOwnerSetupStatus();

  // Don't render if loading or if identifiers are configured
  if (isLoading) return null;
  if (!status) return null;
  if (status.has_telegram || status.has_email) return null;

  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 dark:border-amber-700 dark:bg-amber-950">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
            Owner identity not configured
          </p>
          <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-300">
            Your owner contact has no identifiers (email or Telegram handle). Configure your
            identity so butlers can recognize you across channels.
          </p>
        </div>
        <Button
          asChild
          size="sm"
          variant="outline"
          className="shrink-0 border-amber-400 text-amber-900 hover:bg-amber-100 dark:border-amber-600 dark:text-amber-100"
        >
          <Link to="/contacts?role=owner">Set Up Identity</Link>
        </Button>
      </div>
    </div>
  );
}
