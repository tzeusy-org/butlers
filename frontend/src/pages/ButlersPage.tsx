import { Link } from "react-router";

import type { ButlerSummary } from "@/api/types";
import { useButlers } from "@/hooks/use-butlers";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";

function statusBadge(status: string) {
  switch (status) {
    case "ok":
    case "online":
      return <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">Up</Badge>;
    case "error":
    case "down":
    case "offline":
      return <Badge variant="destructive">Down</Badge>;
    case "degraded":
      return (
        <Badge variant="outline" className="border-amber-500 text-amber-600">
          Degraded
        </Badge>
      );
    default:
      return <Badge variant="secondary">{status}</Badge>;
  }
}

function ButlerCard({ butler }: { butler: ButlerSummary }) {
  const detailPath = `/butlers/${encodeURIComponent(butler.name)}`;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-3">
          <Link to={detailPath} className="hover:underline">
            {butler.name}
          </Link>
          {statusBadge(butler.status)}
        </CardTitle>
        <CardDescription>Butler endpoint on port {butler.port}</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-muted-foreground font-medium">Status</dt>
          <dd className="capitalize">{butler.status}</dd>
          <dt className="text-muted-foreground font-medium">Port</dt>
          <dd>{butler.port}</dd>
        </dl>
      </CardContent>
      <CardFooter>
        <Button variant="outline" size="sm" asChild>
          <Link to={detailPath}>Open details</Link>
        </Button>
      </CardFooter>
    </Card>
  );
}

function LoadingState() {
  return (
    <div className="space-y-4">
      <p className="text-muted-foreground text-sm">Loading butlers...</p>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <Card key={i}>
            <CardHeader>
              <Skeleton className="h-5 w-32" />
              <Skeleton className="h-4 w-44" />
            </CardHeader>
            <CardContent className="space-y-2">
              <Skeleton className="h-4 w-28" />
              <Skeleton className="h-4 w-20" />
            </CardContent>
            <CardFooter>
              <Skeleton className="h-9 w-28" />
            </CardFooter>
          </Card>
        ))}
      </div>
    </div>
  );
}

export default function ButlersPage() {
  const { data: response, isLoading, isError, error } = useButlers();
  const butlers = [...(response?.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  const onlineCount = butlers.filter((b) => b.status === "ok" || b.status === "online").length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Butlers</h1>
        <p className="text-muted-foreground mt-1">
          Browse all registered butlers and jump directly to detail views.
        </p>
      </div>

      {isLoading ? (
        <LoadingState />
      ) : isError ? (
        <Card>
          <CardContent className="py-10">
            <p className="text-sm text-destructive">
              Failed to load butlers. {error instanceof Error ? error.message : "Unknown error"}
            </p>
          </CardContent>
        </Card>
      ) : butlers.length === 0 ? (
        <Card>
          <CardContent className="py-6">
            <EmptyState
              title="No butlers found"
              description="No butlers were returned by the API. Check daemon status and try again."
            />
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-muted-foreground text-sm font-medium">
                  Total Butlers
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{butlers.length}</div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-muted-foreground text-sm font-medium">
                  Healthy
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{onlineCount}</div>
                <p className="text-muted-foreground mt-1 text-xs">
                  {Math.round((onlineCount / butlers.length) * 100)}% currently up
                </p>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {butlers.map((butler) => (
              <ButlerCard key={butler.name} butler={butler} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
