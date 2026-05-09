// BackendSketch — collapsible panel below the demo showing the proposed
// Python/FastAPI route + the React hook + the frontend → backend wire.
// Self-contained reference; this is design intent, not running code.

function BackendSketch({ theme = 'dark' }) {
  const [open, setOpen] = React.useState(true);
  const isDark = theme !== 'light';
  const panelBg = isDark ? 'oklch(0.115 0 0)' : 'oklch(0.965 0.005 85)';
  const codeBg  = isDark ? 'oklch(0.205 0 0)' : 'oklch(1 0 0)';
  const codeFg  = isDark ? 'oklch(0.92 0 0)'  : 'oklch(0.18 0 0)';
  const labelFg = isDark ? 'oklch(0.708 0 0)' : 'oklch(0.46 0 0)';
  const accentFg= isDark ? 'oklch(0.985 0 0)' : 'oklch(0.18 0 0)';
  const borderC = isDark ? 'oklch(1 0 0 / 0.10)' : 'oklch(0 0 0 / 0.10)';

  const py = `# butlers/dashboard/api/briefing.py
#
# GET /api/dashboard/briefing
# Returns a templated headline + LLM-elaborated paragraph.
# Cached per-user for 5 minutes; falls back to templated paragraph
# on LLM failure.

from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from butlers.llm import claude_haiku       # existing thin wrapper
from butlers.dashboard.state import collect_dashboard_state
from butlers.dashboard.briefing.classify import classify, headline_for
from butlers.dashboard.briefing.fallback import elaborate_fallback
from butlers.cache import ttl_cache

router = APIRouter(prefix="/api/dashboard")


class Briefing(BaseModel):
    greet: str
    headline: str
    elaboration: str
    source: str            # "llm" | "fallback"
    generated_at: datetime
    state_class: str       # "quiet" | "mild" | "busy" | "urgent" | ...


@router.get("/briefing", response_model=Briefing)
@ttl_cache(seconds=300, key="user_id")     # 5min per user
async def briefing(user_id: str = Depends(current_user_id)) -> Briefing:
    state = await collect_dashboard_state(user_id)
    cls   = classify(state)
    head  = headline_for(cls)

    try:
        elaboration = await claude_haiku.complete(
            prompt=BRIEFING_PROMPT.format(state=state.compact_json()),
            max_tokens=120,
            temperature=0.4,
            timeout=4.0,
        )
        source = "llm"
    except Exception:
        elaboration = elaborate_fallback(state, cls)
        source = "fallback"

    return Briefing(
        greet=head.greet, headline=head.body,
        elaboration=elaboration.strip(),
        source=source, generated_at=datetime.utcnow(),
        state_class=cls.urgency,
    )


BRIEFING_PROMPT = """You are writing a single short paragraph for a personal
AI dashboard. 1-3 sentences, max 50 words. Past tense for events, present
for state. No exclamation marks. No emoji. No first person. Avoid "your".
No hedging adverbs. Voice: a butler announcing, not a chatbot reporting.

STATE:
{state}

Write only the paragraph."""`;

  const ts = `// frontend/src/hooks/use-briefing.ts
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api";

export function useBriefing() {
  return useQuery({
    queryKey: ["dashboard", "briefing"],
    queryFn: () => apiClient.dashboard.briefing(),
    staleTime: 5 * 60 * 1000,        // matches backend cache
    refetchInterval: 5 * 60 * 1000,  // poll every 5 min
    refetchOnWindowFocus: true,
  });
}

// frontend/src/pages/DashboardPage.tsx (excerpt)
const { data: briefing, refetch, isFetching } = useBriefing();

<h1>
  <span className="text-muted-foreground">{briefing.greet}</span><br/>
  {briefing.headline}
</h1>
<p className="font-serif">{briefing.elaboration}</p>`;

  return (
    <div style={{
      borderTop: `1px solid ${borderC}`,
      background: panelBg, padding: '32px 48px',
      fontFamily: 'var(--font-sans)',
    }}>
      <button onClick={() => setOpen(!open)} style={{
        background: 'transparent', border: 'none', cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 10,
        fontFamily: 'var(--font-mono)', fontSize: 11,
        color: labelFg, textTransform: 'uppercase',
        letterSpacing: '0.12em', padding: 0, marginBottom: open ? 24 : 0,
      }}>
        <span style={{ transform: open ? 'rotate(90deg)' : 'rotate(0)', transition: 'transform 150ms' }}>›</span>
        Backend integration sketch
      </button>

      {open && (
        <div style={{
          maxWidth: 1280, margin: '0 auto',
          display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 32,
        }}>
          <div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 10,
              color: labelFg, letterSpacing: '0.1em',
              textTransform: 'uppercase', marginBottom: 8,
            }}>backend · python / fastapi</div>
            <pre style={{
              background: codeBg,
              border: `1px solid ${borderC}`,
              padding: 16, borderRadius: 4,
              fontFamily: 'var(--font-mono)', fontSize: 11,
              lineHeight: 1.55, color: codeFg,
              overflow: 'auto', maxHeight: 540, margin: 0,
            }}>{py}</pre>
          </div>
          <div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 10,
              color: labelFg, letterSpacing: '0.1em',
              textTransform: 'uppercase', marginBottom: 8,
            }}>frontend · tanstack query hook</div>
            <pre style={{
              background: codeBg,
              border: `1px solid ${borderC}`,
              padding: 16, borderRadius: 4,
              fontFamily: 'var(--font-mono)', fontSize: 11,
              lineHeight: 1.55, color: codeFg,
              overflow: 'auto', maxHeight: 540, margin: 0,
            }}>{ts}</pre>

            <div style={{
              marginTop: 24, fontFamily: 'var(--font-serif)', fontSize: 14,
              lineHeight: 1.6, color: labelFg,
            }}>
              <strong style={{ color: accentFg, fontFamily: 'var(--font-sans)', fontWeight: 600 }}>Why this shape:</strong>
              {' '}the backend is the single owner of LLM credentials, prompt
              versioning, and rate limits. Caching for 5 minutes means a noisy
              dashboard refresh doesn't burn tokens. The fallback path
              guarantees the page always renders something legible — Claude
              being slow or down never blanks the briefing.
              {' '}Status pill in the header tells you which path you're
              looking at, so the source is always honest.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

window.BackendSketch = BackendSketch;
