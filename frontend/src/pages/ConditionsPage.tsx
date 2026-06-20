import ConditionTracker from "@/components/health/ConditionTracker";

export default function ConditionsPage() {
  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <p className="text-muted-foreground font-mono text-[10px] uppercase tracking-[0.14em]">
          Health
        </p>
        <h1 className="text-foreground font-sans text-2xl font-medium leading-tight tracking-[-0.02em]">
          Conditions
        </h1>
        <p className="text-muted-foreground max-w-prose font-serif text-[15px] leading-relaxed">
          What you're carrying, and where each one stands. In sync with your Health butler.
        </p>
      </header>

      <ConditionTracker />
    </div>
  );
}
