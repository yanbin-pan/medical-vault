import { useEffect, useMemo, useRef, useState } from "react";
import type { TimeSeries } from "../lib/api";
import { Tooltip, type TooltipState } from "./Tooltip";
import { displayUnit } from "../lib/units";

const PADDING = { top: 16, right: 20, bottom: 26, left: 48 };
// Extra room on the right for the direct labels drawn beside the final point.
const LABEL_GUTTER = 46;
const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];

interface Props {
  series: TimeSeries[];
  height?: number;
  /** Draws the reference interval printed on the reports as a shaded band. */
  showReferenceBand?: boolean;
}

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) return [min];
  const raw = (max - min) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? magnitude * 10;
  const start = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= max + step * 0.001; v += step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}

function formatValue(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toFixed(0);
  if (abs >= 10) return value.toFixed(1).replace(/\.0$/, "");
  return value.toFixed(2).replace(/0$/, "").replace(/\.$/, "");
}

const DATE = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });

/**
 * A time series with an optional reference band.
 *
 * Hand-written SVG rather than a charting library: it keeps the bundle small
 * enough to serve happily from a Raspberry Pi, and it is one fewer dependency
 * to still be maintained in ten years, which is the whole premise here.
 */
export function LineChart({ series, height = 220, showReferenceBand = true }: Props) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(520);

  // useEffect, not useMemo: a cleanup function returned from useMemo is never
  // called, which would leak an observer per mount.
  useEffect(() => {
    const node = wrapper.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const model = useMemo(() => {
    const all = series.flatMap((s) => s.points);
    if (all.length === 0) return null;

    const times = all.map((p) => new Date(p.t).getTime());
    const values = all.map((p) => p.value);

    const band = showReferenceBand
      ? series
          .flatMap((s) => s.points)
          .reduce<{ low: number | null; high: number | null }>(
            (acc, p) => ({
              // The most recent printed range wins: laboratories revise them.
              low: p.reference_low ?? acc.low,
              high: p.reference_high ?? acc.high,
            }),
            { low: null, high: null },
          )
      : { low: null, high: null };

    const candidates = [...values, band.low, band.high].filter(
      (v): v is number => v !== null && Number.isFinite(v),
    );
    let min = Math.min(...candidates);
    let max = Math.max(...candidates);
    if (min === max) {
      min -= Math.abs(min) * 0.1 || 1;
      max += Math.abs(max) * 0.1 || 1;
    }
    const pad = (max - min) * 0.12;
    min -= pad;
    max += pad;

    const tMin = Math.min(...times);
    const tMax = Math.max(...times);
    const gutter = series.length > 1 ? LABEL_GUTTER : 0;
    const innerW = Math.max(width - PADDING.left - PADDING.right - gutter, 10);
    const innerH = height - PADDING.top - PADDING.bottom;

    const x = (t: number) =>
      PADDING.left + (tMax === tMin ? innerW / 2 : ((t - tMin) / (tMax - tMin)) * innerW);
    const y = (v: number) => PADDING.top + innerH - ((v - min) / (max - min)) * innerH;

    return { x, y, min, max, tMin, tMax, innerW, innerH, band };
  }, [series, width, height, showReferenceBand]);

  if (!model) {
    return <p className="muted small">No numeric readings to plot yet.</p>;
  }

  const { x, y, min, max, band, innerW, innerH } = model;
  const ticks = niceTicks(min, max);
  // Every distinct visit, used for the crosshair.
  const visits = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.t)))).sort();

  const handleMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const localX = event.clientX - rect.left;
    let nearest = 0;
    let best = Infinity;
    visits.forEach((visit, index) => {
      const distance = Math.abs(x(new Date(visit).getTime()) - localX);
      if (distance < best) {
        best = distance;
        nearest = index;
      }
    });
    setHoverIndex(nearest);
    const visit = visits[nearest];
    setTooltip({
      x: event.clientX,
      y: event.clientY,
      title: DATE.format(new Date(visit)),
      rows: series
        .map((s) => {
          const point = s.points.find((p) => p.t === visit);
          if (!point) return null;
          const flag = point.abnormal_flag ? ` (${point.abnormal_flag})` : "";
          const unit = displayUnit(s.unit);
          return `${s.label}: ${formatValue(point.value)}${unit ? ` ${unit}` : ""}${flag}`;
        })
        .filter((row): row is string => row !== null),
    });
  };

  const clear = () => {
    setTooltip(null);
    setHoverIndex(null);
  };

  const hoveredTime = hoverIndex !== null ? new Date(visits[hoverIndex]).getTime() : null;

  return (
    <div ref={wrapper}>
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        onMouseMove={handleMove}
        onMouseLeave={clear}
        role="img"
        aria-label={`${series.map((s) => s.label).join(", ")} over time`}
        style={{ display: "block", touchAction: "none" }}
      >
        {/* Reference band from the printed interval, behind everything. */}
        {band.low !== null && band.high !== null && (
          <>
            <rect
              x={PADDING.left}
              y={y(band.high)}
              width={innerW}
              height={Math.max(y(band.low) - y(band.high), 1)}
              fill="var(--status-good)"
              opacity={0.07}
            />
            <line
              x1={PADDING.left} x2={PADDING.left + innerW}
              y1={y(band.high)} y2={y(band.high)}
              stroke="var(--status-good)" strokeWidth={1} strokeDasharray="3 3" opacity={0.5}
            />
            <line
              x1={PADDING.left} x2={PADDING.left + innerW}
              y1={y(band.low)} y2={y(band.low)}
              stroke="var(--status-good)" strokeWidth={1} strokeDasharray="3 3" opacity={0.5}
            />
          </>
        )}

        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={PADDING.left} x2={PADDING.left + innerW}
              y1={y(tick)} y2={y(tick)}
              stroke="var(--gridline)" strokeWidth={1}
            />
            <text
              x={PADDING.left - 8} y={y(tick)} dy="0.32em"
              textAnchor="end" fontSize={11} fill="var(--text-muted)"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {formatValue(tick)}
            </text>
          </g>
        ))}

        <line
          x1={PADDING.left} x2={PADDING.left + innerW}
          y1={PADDING.top + innerH} y2={PADDING.top + innerH}
          stroke="var(--baseline)" strokeWidth={1}
        />

        {[visits[0], visits[visits.length - 1]].map((visit, index) =>
          visit ? (
            <text
              key={`${visit}-${index}`}
              x={index === 0 ? PADDING.left : PADDING.left + innerW}
              y={height - 8}
              textAnchor={index === 0 ? "start" : "end"}
              fontSize={11}
              fill="var(--text-muted)"
            >
              {new Date(visit).getFullYear()}
            </text>
          ) : null,
        )}

        {hoveredTime !== null && (
          <line
            x1={x(hoveredTime)} x2={x(hoveredTime)}
            y1={PADDING.top} y2={PADDING.top + innerH}
            stroke="var(--text-muted)" strokeWidth={1} strokeDasharray="2 3"
          />
        )}

        {series.map((s, index) => {
          const color = SERIES_COLORS[index % SERIES_COLORS.length];
          const path = s.points
            .map((p, i) => `${i === 0 ? "M" : "L"} ${x(new Date(p.t).getTime())} ${y(p.value)}`)
            .join(" ");
          const last = s.points[s.points.length - 1];
          return (
            <g key={s.series_key}>
              <path
                d={path} fill="none" stroke={color}
                strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
              />
              {s.points.map((p) => {
                const abnormal = p.abnormal_flag && p.abnormal_flag !== "normal";
                return (
                  <circle
                    key={p.document_id + p.t}
                    cx={x(new Date(p.t).getTime())}
                    cy={y(p.value)}
                    r={abnormal ? 5 : 4}
                    fill={abnormal ? "var(--status-critical)" : color}
                    /* A 2px surface ring keeps overlapping points legible. */
                    stroke="var(--surface-1)"
                    strokeWidth={2}
                  />
                );
              })}
              {/* Direct label in the right-hand gutter, vertically at the final
                  point. Placing it beside the line rather than above it stops
                  two series' labels landing on top of each other when their
                  last readings are close together. */}
              {last && series.length > 1 && (
                <text
                  x={x(new Date(last.t).getTime()) + 8}
                  y={y(last.value)}
                  dy="0.32em"
                  textAnchor="start"
                  fontSize={11}
                  fill="var(--text-secondary)"
                >
                  {s.laterality ?? s.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <Tooltip state={tooltip} />
    </div>
  );
}
