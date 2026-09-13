import { Badge } from "@/components/ui/badge";

export function PurposeLaneBadge({
  lane,
}: {
  lane: "standard" | "private_content" | null | undefined;
}) {
  if (!lane) return <span className="text-xs text-muted-foreground">—</span>;
  const isPrivate = lane === "private_content";
  const label = isPrivate ? "Private content" : "Standard";
  return (
    <Badge
      variant="outline"
      aria-label={`Purpose lane: ${label}`}
      className={
        isPrivate
          ? "border-[var(--amber)]/40 bg-[var(--amber)]/10 text-[var(--amber)]"
          : "text-muted-foreground"
      }
    >
      {label}
    </Badge>
  );
}
