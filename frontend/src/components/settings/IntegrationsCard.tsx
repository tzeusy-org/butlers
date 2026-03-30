/**
 * IntegrationsCard — consolidated card for system-level external service integrations.
 *
 * Renders WhatsApp, Steam, and OwnTracks as sections within a single card on the
 * settings page. Google OAuth, Spotify, and Home Assistant have moved to the
 * Secrets page (User tab → Integrations section) as they are identity-bound.
 */

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import { OwnTracksSection } from "./OwnTracksSetupCard";
import { SteamSection } from "./SteamSetupCard";
import { WhatsAppSection } from "./WhatsAppSetupCard";

export function IntegrationsCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Integrations</CardTitle>
        <CardDescription>
          Connect external services to your butlers.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="divide-y">
          <div className="pb-6">
            <WhatsAppSection />
          </div>
          <div className="py-6">
            <SteamSection />
          </div>
          <div className="pt-6">
            <OwnTracksSection />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
