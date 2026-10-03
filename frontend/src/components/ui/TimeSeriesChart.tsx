// ---------------------------------------------------------------------------
// TimeSeriesChart: the one trend-chart grammar (bu-q7vx1q.6)
//
// Time-proportional x axis ending at the series window end, a mark on every
// observation, linear joins (never smoothed), line breaks where the caller's
// maxGap was exceeded, and a muted, labelled tail from the last reading to the
// axis end so staleness never reads as current data.
// ---------------------------------------------------------------------------

import { useId, type ReactElement } from "react";
import { formatInTimeZone } from "date-fns-tz";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useTimezone } from "@/components/ui/timezone-context";
import { DAY_MS, tailLabel, type TimeSeries } from "@/lib/time-series";

/** Marks are drawn on every observation up to this many points. */
const DOT_LIMIT = 40;
const TICK_COUNT = 4;

export interface TimeSeriesLine {
  key: string;
  name: string;
  stroke: string;
  strokeOpacity?: number;
  strokeDasharray?: string;
  /** Area variant only: a solid fill replacing the default fading gradient. */
  fill?: string;
}

interface TimeSeriesChartProps {
  series: TimeSeries<string>;
  lines: TimeSeriesLine[];
  variant?: "line" | "area";
  join?: "linear" | "step";
  height: number;
  /** Sparkline mode: no axes or grid, tooltip only. */
  compact?: boolean;
  yDomain?: [number | "auto", number | "auto"];
  yUnit?: string;
  tooltip?: ReactElement;
  animate?: boolean;
  testId?: string;
}

function tickValues([start, end]: [number, number]): number[] {
  const step = (end - start) / (TICK_COUNT - 1);
  return Array.from({ length: TICK_COUNT }, (_, i) => Math.round(start + step * i));
}

export function TimeSeriesChart({
  series,
  lines,
  variant = "line",
  join = "linear",
  height,
  compact = false,
  yDomain,
  yUnit,
  tooltip,
  animate = false,
  testId,
}: TimeSeriesChartProps) {
  const timezone = useTimezone();
  // Unique per instance so several charts on one page never share a gradient;
  // colons from useId are not safe inside url(#...).
  const uid = useId().replace(/:/g, "");
  const gradientId = (key: string) => `ts-fade-${uid}-${key}`;
  const { rows, domain, observationCount, lastReadingAt, tailDays } = series;
  const label = tailLabel(tailDays);
  const showDots = observationCount <= DOT_LIMIT;
  const lineType = join === "step" ? "stepAfter" : "linear";
  const spanDays = (domain[1] - domain[0]) / DAY_MS;
  const formatTick = (ms: number) =>
    formatInTimeZone(ms, timezone, spanDays < 2 ? "HH:mm" : "MMM d");

  const axes = (
    <>
      {!compact && <CartesianGrid strokeDasharray="3 3" className="stroke-border/60" />}
      <XAxis
        dataKey="x"
        type="number"
        scale="time"
        domain={domain}
        ticks={tickValues(domain)}
        tickFormatter={formatTick}
        hide={compact}
        className="text-xs"
      />
      <YAxis hide={compact} domain={yDomain} unit={yUnit} className="text-xs" />
      <Tooltip
        content={tooltip}
        labelFormatter={(l) => formatTick(Number(l))}
        isAnimationActive={animate}
      />
      {label && lastReadingAt !== null && (
        <ReferenceArea
          x1={lastReadingAt}
          x2={domain[1]}
          className="fill-muted-foreground/10"
          ifOverflow="hidden"
        />
      )}
    </>
  );

  const margin = compact
    ? { top: 4, right: 4, bottom: 4, left: 4 }
    : { top: 5, right: 20, bottom: 5, left: 0 };

  return (
    <div data-testid={testId}>
      <ResponsiveContainer width="100%" height={height}>
        {variant === "area" ? (
          <AreaChart data={rows} margin={margin}>
            <defs>
              {lines.map((l) => (
                <linearGradient key={l.key} id={gradientId(l.key)} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={l.stroke} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={l.stroke} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            {axes}
            {lines.map((l) => (
              <Area
                key={l.key}
                dataKey={l.key}
                name={l.name}
                type={lineType}
                stroke={l.stroke}
                strokeWidth={compact ? 1.5 : 2}
                fill={l.fill ?? `url(#${gradientId(l.key)})`}
                dot={showDots}
                connectNulls={false}
                isAnimationActive={animate}
              />
            ))}
          </AreaChart>
        ) : (
          <LineChart data={rows} margin={margin}>
            {axes}
            {lines.map((l) => (
              <Line
                key={l.key}
                dataKey={l.key}
                name={l.name}
                type={lineType}
                stroke={l.stroke}
                strokeOpacity={l.strokeOpacity}
                strokeDasharray={l.strokeDasharray}
                strokeWidth={compact ? 1.5 : 2}
                dot={showDots}
                connectNulls={false}
                isAnimationActive={animate}
              />
            ))}
          </LineChart>
        )}
      </ResponsiveContainer>
      {label && (
        <p className="font-mono text-xs text-muted-foreground" data-testid="time-series-tail">
          {label}
        </p>
      )}
    </div>
  );
}

/** Owner-timezone date for custom tooltips; x is the axis value in epoch ms. */
export function TooltipDate({ x }: { x: number }) {
  const timezone = useTimezone();
  return <>{formatInTimeZone(x, timezone, "MMM d, yyyy")}</>;
}
