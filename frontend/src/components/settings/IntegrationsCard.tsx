/**
 * IntegrationsCard — consolidated card for all external service integrations.
 *
 * Renders Google OAuth, WhatsApp, Spotify, and OwnTracks as sections
 * within a single card on the settings page.
 */

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import { GoogleOAuthSection } from "./GoogleOAuthSection";
import { OwnTracksSection } from "./OwnTracksSetupCard";
import { SpotifySection } from "./SpotifySetupCard";
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
            <GoogleOAuthSection />
          </div>
          <div className="py-6">
            <WhatsAppSection />
          </div>
          <div className="py-6">
            <SpotifySection />
          </div>
          <div className="pt-6">
            <OwnTracksSection />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
