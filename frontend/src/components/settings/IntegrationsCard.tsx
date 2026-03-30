/**
 * IntegrationsCard — consolidated card for system-level external service integrations.
 *
 * All integration cards have been moved to the Secrets page (User tab →
 * Integrations section). This card will be removed by bu-um75.4.
 */

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";


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
          <div className="py-6 text-muted-foreground text-sm">
            All integrations have been moved to the Secrets &gt; User tab.
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
