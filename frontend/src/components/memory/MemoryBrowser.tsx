import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import EpisodesRegister from "@/components/memory/EpisodesRegister";
import FactsRegister from "@/components/memory/FactsRegister";
import RulesRegister from "@/components/memory/RulesRegister";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface MemoryBrowserProps {
  /** When set, filter all queries to this butler scope. */
  butlerScope?: string;
}

// ---------------------------------------------------------------------------
// MemoryBrowser
// ---------------------------------------------------------------------------

export default function MemoryBrowser({ butlerScope }: MemoryBrowserProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Memory Browser</CardTitle>
        <CardDescription>
          Browse facts, rules, and episodes across the memory subsystem
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="facts">
          <TabsList>
            <TabsTrigger value="facts">Facts</TabsTrigger>
            <TabsTrigger value="rules">Rules</TabsTrigger>
            <TabsTrigger value="episodes">Episodes</TabsTrigger>
          </TabsList>

          <TabsContent value="facts">
            <FactsRegister butlerScope={butlerScope} />
          </TabsContent>

          <TabsContent value="rules">
            <RulesRegister butlerScope={butlerScope} />
          </TabsContent>

          <TabsContent value="episodes">
            <EpisodesRegister butlerScope={butlerScope} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
