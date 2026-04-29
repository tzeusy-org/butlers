# The Health Butler

Your personal health companion that remembers what matters to you.

## What We Believe

Your health is not a snapshot—it's a story told over weeks, months, and years. Small measurements, a new symptom, a medication change, a pattern you almost noticed—these pieces only reveal their meaning when they're held together. Most tools ask you to forget, to start over each day. We believe differently.

The Health Butler is patient and non-judgmental. It exists to listen, remember, and help you see what you might otherwise miss.

## What It Does

The Health Butler tracks the full picture of your wellbeing:

- **Measurements**: Weight, blood pressure, glucose, temperature—any metric that matters to you. It keeps the history so you can see the trajectory.
- **Medications**: What you take, how often, and whether you're staying on track. It knows that adherence is hard, and helps you understand your real patterns.
- **Conditions**: The active health issues you're managing, and how they're progressing. It holds the context so you don't have to.
- **Symptoms**: When you're not feeling right, log it with a severity level. Over time, patterns emerge that you and your doctor might have missed.
- **Nutrition**: What you eat matters. The butler tracks your meals, aggregates your nutrition, and helps you understand your patterns.
- **Research**: You find health articles, studies, or advice online. Save them here so you have them when you need them.

## Why It Matters

**Continuity**: Health decisions happen across seasons. Your doctor asks, "How have you been feeling?" The Health Butler has the answer—not a vague memory, but the data. This is power.

**Agency**: Your health data is yours. You decide what to log, what to share, and what to keep private. There's no judgment in the butler's memory, only honesty.

**Insight**: Patterns hide in plain sight. The butler spots trends you'd miss—a symptom that only happens on Mondays, measurements that drift slowly upward, medication adherence that falters before you catch it. These insights help you and your healthcare team make better decisions.

## A Companion, Not a Doctor

The Health Butler is here to help you understand yourself. It's not a replacement for your doctor, your therapist, or your judgment. It's a tool that makes the invisible visible, so you can bring better information to every health conversation.

## Start Small

You don't have to log everything at once. Start with what matters most to you—maybe it's your weight, your medications, or your mood. As you build the habit, you'll see the value compound.

Over time, the Health Butler becomes an extension of your memory. It remembers so you can focus on living.

---

## Meal Write Path (Technical Reference)

**Who writes:** The user (or an agent acting on their behalf) calls the `meal_log` MCP tool via an interactive channel (Telegram, direct MCP call, or an in-session tool call). There is no external connector — meals are always entered manually.

**Trigger:** Explicit user action (e.g. "Log my lunch — pasta with tomato sauce" → Telegram → Health Butler session → `meal_log` tool call).

**Dual-write contract:** Every `meal_log` call writes to two storage surfaces:

1. **`facts` table** (memory module, `health` scope) — powers `meal_history`, `nutrition_summary`, semantic search, and the weekly health summary. The fact predicate is `meal_{type}` (e.g. `meal_lunch`).
2. **`health.meals` table** — the Chronicler evidence surface. The `MealsAdapter` reads this table on every `chronicler_project_meals` tick and projects each row into `chronicler.point_events` as an `eating_event`. This powers the Meal lane on the Chronicles dashboard.

**Latency:** Meals appear on the Chronicles dashboard within one `chronicler_project_meals` tick (configured frequency; typically ≤ 15 minutes after logging).

**Failure semantics:** If the `health.meals` dual-write fails (e.g. migration not yet applied), the meal is still persisted in `facts` and a warning is logged. The Chronicles Meal lane will be missing that entry until it is replayed or the table is available.
