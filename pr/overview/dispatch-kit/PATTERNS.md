# Dispatch — Patterns

Lift these directly. They use Tailwind 4 + shadcn conventions matching
the Butlers codebase. Read `DESIGN_LANGUAGE.md` for the *why*; this doc
is the *how*.

> Rule of thumb: if a new pattern doesn't appear here, derive it from an
> existing one. Do not invent.

---

## 1. Page shell

Every page lives inside the same shell. The shell is owned by `Shell.tsx`;
the page only writes the body.

```tsx
// Page body wrapper
<div className="mx-auto grid max-w-[1280px] grid-cols-[1.4fr_1fr] gap-14 px-14 py-12">
  <LeftColumn />
  <RightColumn />
</div>
```

Single-column variant (Audit, Settings):

```tsx
<div className="mx-auto max-w-[1280px] px-14 py-12">
  <article className="max-w-[72ch]">{/* ... */}</article>
</div>
```

---

## 2. Eyebrow

Used to title sections without a heading. Always mono uppercase.

```tsx
<div className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
  <span>Approvals · 7 may 2026</span>
  <span className="flex-1" />
  <BriefingStatus /* optional right-side slot */ />
</div>
```

---

## 3. Display headline

Two-line. Greet on top in muted; body below in foreground.

```tsx
<h1 className="max-w-[14ch] font-sans text-[44px] font-medium leading-[1.08] tracking-[-0.025em]">
  <span className="text-muted-foreground">Good afternoon.</span>
  <br />
  Things are quiet, with two exceptions.
</h1>
```

**Never** use `font-bold`. Display weight is 500, period.

---

## 4. Voice paragraph

The serif "voice" — used for LLM elaboration, "why this shape" gloss,
and serif-italic empty states.

```tsx
<p
  aria-live="polite"
  className="max-w-[50ch] font-serif text-base leading-relaxed text-muted-foreground transition-opacity duration-200"
  style={{ opacity: loading ? 0.4 : 1 }}
>
  {text}
</p>
```

Empty state (italic):

```tsx
<p className="font-serif italic text-muted-foreground">Nothing waiting.</p>
```

---

## 5. Canonical list row

The single most-used primitive. Grid: mark / content / meta. Hairline
between rows. **Not** a card.

```tsx
<ul className="divide-y divide-border/60">
  {items.map(item => (
    <li key={item.id}
        className="grid grid-cols-[24px_1fr_auto] items-start gap-4 py-4 hover:bg-foreground/[0.04]">
      {/* mark — severity glyph, status dot, time, or letter-mark */}
      <SeverityGlyph level={item.severity} />

      {/* content — title + serif detail */}
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-sm">{item.title}</span>
          <span className="font-mono text-[10px] text-muted-foreground tnum">{item.age}</span>
        </div>
        <div className="font-serif text-[13px] text-muted-foreground">{item.detail}</div>
      </div>

      {/* meta — action arrow or numeric */}
      <a href={item.href} className="text-sm underline decoration-border-strong underline-offset-4">
        {item.action} →
      </a>
    </li>
  ))}
</ul>
```

---

## 6. KPI strip

Four-column hairline-divided strip. Tabular nums, mono eyebrow, sans
mega-number, mono delta. **No card around it.**

```tsx
<div className="grid grid-cols-4 divide-x divide-border border-y border-border">
  {kpis.map(k => (
    <div key={k.label} className="px-6 py-5">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {k.label}
      </div>
      <div className="mt-2 text-[32px] font-medium tabular-nums tracking-[-0.03em] leading-none">
        {k.value}
      </div>
      <div className="mt-1 font-mono text-[10px] text-muted-foreground tnum">
        {k.delta}
      </div>
    </div>
  ))}
</div>
```

---

## 7. Section (right-column index)

Quiet list with eyebrow title and bottom hairline.

```tsx
<section>
  <header className="mb-2 flex items-baseline border-b border-border pb-2">
    <h2 className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
      Butlers
    </h2>
    <span className="flex-1" />
    <a href="/butlers" className="font-mono text-[10px] text-muted-foreground underline-offset-4 hover:underline">
      all →
    </a>
  </header>
  <ul className="divide-y divide-border/60">
    {butlers.map(b => (
      <li key={b.name}
          className="grid grid-cols-[8px_1fr_auto_auto] items-center gap-3 py-2.5 text-sm">
        <StatusDot status={b.status} />
        <span className="capitalize">{b.name}</span>
        <span className="font-mono text-[11px] text-muted-foreground tnum">{b.sessions24h}</span>
        <span className="font-mono text-[11px] tnum">${b.spendToday.toFixed(2)}</span>
      </li>
    ))}
  </ul>
</section>
```

---

## 8. Status dot

```tsx
const STATUS_COLOR = {
  ok:       'bg-[var(--green)]',
  degraded: 'bg-[var(--amber)]',
  error:    'bg-[var(--red)]',
  waiting:  'bg-muted-foreground',
};
function StatusDot({ status, size = 6 }) {
  return (
    <span className={`inline-block shrink-0 rounded-full ${STATUS_COLOR[status]}`}
          style={{ width: size, height: size }} />
  );
}
```

---

## 9. Severity glyph

A 6px square — used inside attention-row gutters where dots conflict.

```tsx
const SEV_COLOR = { high: 'var(--red)', medium: 'var(--amber)', low: 'var(--mfg)' };
function Sev({ level, size = 6 }) {
  return <span className="inline-block shrink-0 rounded-[1px]"
               style={{ width: size, height: size, background: SEV_COLOR[level] }} />;
}
```

---

## 10. Letter-mark (butler glyph)

The **only** place butler hues exist. Two tones.

```tsx
const CAT = {
  relationship: 'var(--category-1)', memory: 'var(--category-2)',
  calendar:     'var(--category-3)', health: 'var(--category-4)',
  household:    'var(--category-5)', education: 'var(--category-6)',
  qa:           'var(--category-7)', chronicler: 'var(--category-8)',
};
function ButlerMark({ name, size = 16, tone = 'neutral' }) {
  const ch = name[0].toUpperCase();
  const hue = CAT[name] ?? 'var(--fg)';
  const fill = tone === 'fill';
  return (
    <span className="inline-flex shrink-0 items-center justify-center rounded-[4px] font-semibold"
          style={{
            width: size, height: size,
            fontSize: Math.round(size * 0.6),
            background: fill ? hue : 'transparent',
            color: fill ? '#fff' : hue,
            border: fill ? '1px solid transparent' : '1px solid var(--border)',
          }}>
      {ch}
    </span>
  );
}
```

---

## 11. Status pill (process indicator)

Anywhere the system reports on its own process — cache age, last sync,
model version. Tiny mono pill, dot + label + ↻.

```tsx
function StatusPill({ source, loading, onRefresh }) {
  const cfg = loading
    ? { dot: 'var(--amber)', label: 'composing…' }
    : source === 'llm'
    ? { dot: 'var(--green)', label: 'llm · cached 5m' }
    : { dot: 'var(--dim)',   label: 'templated' };
  return (
    <button onClick={onRefresh}
      className="inline-flex items-center gap-1.5 rounded-[3px] border border-border-strong px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-muted-foreground hover:text-foreground">
      <span className="size-1.5 rounded-full" style={{ background: cfg.dot }} />
      {cfg.label}
      <span aria-hidden>↻</span>
    </button>
  );
}
```

---

## 12. Sidebar rail item

```tsx
<a href={item.path}
   aria-label={item.label}
   className="relative flex h-9 items-center justify-center text-muted-foreground hover:text-foreground"
   style={{ borderLeft: active ? '2px solid var(--fg)' : '2px solid transparent',
            background: active ? 'oklch(1 0 0 / 0.06)' : 'transparent' }}>
  {item.butler
    ? <ButlerMark name={item.butler} tone={active ? 'fill' : 'neutral'} />
    : <Icon name={item.icon} />}
  {/* status dot, badge — absolutely positioned */}
</a>
```

Tooltip floats out at `left: 56px` on hover/focus-visible only.

---

## 13. Pill button (filters, toggles)

```tsx
<button className={`rounded-[3px] border border-border-strong px-2.5 py-1 font-mono text-[11px] ${
  active ? 'bg-foreground text-background' : 'text-foreground hover:bg-foreground/[0.06]'
}`}>{label}</button>
```

## 14. Commit button (Approve, Send, Re-authorize)

```tsx
<button className="rounded-[3px] bg-foreground px-3 py-1.5 text-[12px] font-medium text-background hover:bg-foreground/90">
  {label}
</button>
```

At most one per surface.

---

## 15. Action arrow link (the row-end "go look")

```tsx
<a href={href} className="text-sm underline decoration-border-strong underline-offset-4 hover:decoration-foreground">
  {label} →
</a>
```

---

## 16. Empty state

One sentence. Serif italic. No illustration.

```tsx
<div className="py-8">
  <p className="font-serif italic text-muted-foreground">Nothing waiting.</p>
</div>
```

---

## 17. The forbidden patterns

Do not write any of these:

```tsx
// ❌ Card-on-card
<Card><Card>…</Card></Card>

// ❌ Welcome banner
<h1>Welcome back, {user.name}!</h1>

// ❌ Gradient hero
<div className="bg-gradient-to-r from-purple-500 to-pink-500">…

// ❌ Italic-serif headline as brand move
<h1 className="font-serif italic">Welcome, <em>Tze</em></h1>

// ❌ Animated count-up KPIs
<CountUp from={0} to={value} duration={1.5} />

// ❌ Drop-shadow card
<div className="rounded-xl border bg-card shadow-md">…

// ❌ Color on background fills
<div className="bg-amber-500/10 border-amber-500">…

// ❌ Emoji in chrome
<button>✨ Try the new feature</button>
```

If the page calls for one of these, the page is wrong before the code is.
