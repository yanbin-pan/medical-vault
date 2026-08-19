import { useMemo, useState } from "react";
import type { Correlation } from "../lib/api";
import { Tooltip, type TooltipState } from "./Tooltip";

/**
 * Correlation is a diverging quantity, so the ramp is a diverging one: blue for
 * negative, red for positive, a neutral grey at zero. A rainbow here would
 * invent structure the numbers do not have, and a single-hue ramp would make
 * -0.9 and +0.9 look like the same finding.
 */
function rampColor(r: number | null): string {
  if (r === null) return "var(--surface-1)";
  const magnitude = Math.abs(r);
  const stops =
    r < 0
      ? ["var(--div-neg-1)", "var(--div-neg-2)", "var(--div-neg-3)"]
      : ["var(--div-pos-1)", "var(--div-pos-2)", "var(--div-pos-3)"];
  if (magnitude < 0.2) return "var(--div-mid)";
  if (magnitude < 0.5) return stops[0];
  if (magnitude < 0.75) return stops[1];
  return stops[2];
}

/** Dark fills need light ink; the two mid steps are light enough for dark ink. */
function inkOn(r: number | null): string {
  if (r === null) return "var(--text-muted)";
  const magnitude = Math.abs(r);
  return magnitude >= 0.5 ? "#ffffff" : "var(--text-primary)";
}

interface Props {
  correlations: Correlation[];
  /** Show Spearman instead of Pearson; useful when one outlier dominates. */
  method?: "pearson" | "spearman";
}

export function CorrelationMatrix({ correlations, method = "pearson" }: Props) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  const { labels, lookup } = useMemo(() => {
    const seen = new Map<string, string>();
    for (const pair of correlations) {
      seen.set(pair.series_a, pair.label_a);
      seen.set(pair.series_b, pair.label_b);
    }
    const keys = [...seen.keys()].sort((a, b) => seen.get(a)!.localeCompare(seen.get(b)!));
    const table = new Map<string, Correlation>();
    for (const pair of correlations) {
      table.set(`${pair.series_a}|${pair.series_b}`, pair);
      table.set(`${pair.series_b}|${pair.series_a}`, pair);
    }
    return { labels: keys.map((k) => ({ key: k, label: seen.get(k)! })), lookup: table };
  }, [correlations]);

  if (correlations.length === 0) {
    return (
      <p className="muted small">
        Nothing to correlate yet. Two measurements need at least four visits where
        both were taken before a coefficient means anything.
      </p>
    );
  }

  const cell = 44;
  const labelWidth = 210;
  const size = labels.length * cell;
  // The column headers are rotated -55 degrees, so the rightmost one runs well
  // past the last cell. Without this the final header is clipped by the SVG.
  const headerOverhang = 130;

  return (
    <div style={{ overflowX: "auto" }}>
      <svg
        width={labelWidth + size + headerOverhang}
        height={size + 150}
        role="img"
        aria-label="Correlation matrix"
      >
        {labels.map((row, i) => (
          <text
            key={`row-${row.key}`}
            x={labelWidth - 8}
            y={150 + i * cell + cell / 2}
            dy="0.32em"
            textAnchor="end"
            fontSize={12}
            fill="var(--text-secondary)"
          >
            {row.label.length > 30 ? `${row.label.slice(0, 29)}…` : row.label}
          </text>
        ))}

        {labels.map((column, j) => (
          <text
            key={`col-${column.key}`}
            x={labelWidth + j * cell + cell / 2}
            y={142}
            fontSize={12}
            fill="var(--text-secondary)"
            transform={`rotate(-55 ${labelWidth + j * cell + cell / 2} 142)`}
            textAnchor="start"
          >
            {column.label.length > 22 ? `${column.label.slice(0, 21)}…` : column.label}
          </text>
        ))}

        {labels.map((row, i) =>
          labels.map((column, j) => {
            const isDiagonal = row.key === column.key;
            const pair = isDiagonal ? null : lookup.get(`${row.key}|${column.key}`);
            const value = isDiagonal ? 1 : (pair ? pair[method] : null);
            const x = labelWidth + j * cell;
            const y = 150 + i * cell;
            return (
              <g
                key={`${row.key}-${column.key}`}
                onMouseEnter={(event) =>
                  setTooltip({
                    x: event.clientX,
                    y: event.clientY,
                    title: isDiagonal ? row.label : `${row.label} × ${column.label}`,
                    rows: pair
                      ? [
                          `Pearson r = ${pair.pearson ?? "—"}`,
                          `Spearman ρ = ${pair.spearman ?? "—"}`,
                          `${pair.n} paired visits`,
                        ]
                      : isDiagonal
                        ? ["Same measurement"]
                        : ["Never measured at the same visit"],
                  })
                }
                onMouseLeave={() => setTooltip(null)}
              >
                {/* 2px gap between cells, drawn as inset rather than a stroke. */}
                <rect
                  x={x + 1}
                  y={y + 1}
                  width={cell - 2}
                  height={cell - 2}
                  rx={4}
                  fill={isDiagonal ? "var(--gridline)" : rampColor(value)}
                />
                {value !== null && !isDiagonal && (
                  <text
                    x={x + cell / 2}
                    y={y + cell / 2}
                    dy="0.32em"
                    textAnchor="middle"
                    fontSize={11}
                    fill={inkOn(value)}
                    style={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    {value.toFixed(2).replace("0.", ".").replace("-.", "−.")}
                  </text>
                )}
              </g>
            );
          }),
        )}
      </svg>

      <div className="legend" aria-hidden="true">
        <span>−1</span>
        {["var(--div-neg-3)", "var(--div-neg-2)", "var(--div-neg-1)", "var(--div-mid)",
          "var(--div-pos-1)", "var(--div-pos-2)", "var(--div-pos-3)"].map((color) => (
          <span key={color} className="swatch" style={{ background: color, width: 22 }} />
        ))}
        <span>+1</span>
        <span className="muted">
          blue: move oppositely · red: move together
        </span>
      </div>
      <Tooltip state={tooltip} />
    </div>
  );
}
