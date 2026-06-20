import ResearchTracker from "@/components/health/ResearchTracker";

export default function ResearchPage() {
  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <p className="text-muted-foreground font-mono text-[10px] uppercase tracking-[0.14em]">
          Health
        </p>
        <h1 className="text-foreground font-sans text-2xl font-medium leading-tight tracking-[-0.02em]">
          Research
        </h1>
        <p className="text-muted-foreground max-w-prose font-serif text-[15px] leading-relaxed">
          Notes, articles, and references you've gathered. In sync with your Health butler.
        </p>
      </header>

      <ResearchTracker />
    </div>
  );
}
