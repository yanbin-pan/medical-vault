import { useState } from "react";
import type { TimeSeries } from "../lib/api";
import { LineChart } from "./LineChart";
import { displayUnit, hasUnit } from "../lib/units";

const DATE = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });

function formatValue(value: number): string {
  return Math.abs(value) >= 10 ? value.toFixed(1).replace(/\.0$/, "") : value.toFixed(2);
}

/**
 * One measurement over time.
 *
 * Every chart carries a table view. Two of the palette's slots sit below 3:1
 * against the light surface, and the palette's relief rule requires visible
 * labels or a table wherever that happens — so the table is not optional
 * decoration, it is the accessibility channel.
 */
export function SeriesCard({ series }: { series: TimeSeries[] }) {
  const [showTable, setShowTable] = useState(false);
  const first = series[0];
  const latest = first.points[first.points.length - 1];
  const trend = first.trend_per_year;

  const rawLabels = [...new Set(series.flatMap((s) => s.label_raw_examples))];

  return (
    <section className="card">
      <div className="chart-head">
        <div>
          <h3 className="chart-title">
            {series.length > 1 ? first.label.replace(/\s*\(.*\)$/, "") : first.label}
            {hasUnit(first.unit) ? <span className="muted"> · {displayUnit(first.unit)}</span> : null}
          </h3>
          <p className="chart-sub">
            {rawLabels.slice(0, 2).join(" / ")}
            {!first.is_mapped && (
              <span className="pill warn" style={{ marginLeft: 6 }}>
                not in catalogue
              </span>
            )}
          </p>
        </div>
        <button
          onClick={() => setShowTable((v) => !v)}
          aria-expanded={showTable}
          title="Show the numbers as a table"
        >
          {showTable ? "Chart" : "Table"}
        </button>
      </div>

      {showTable ? (
        <table className="data">
          <thead>
            <tr>
              <th>Date</th>
              {series.length > 1 && <th>Side</th>}
              <th style={{ textAlign: "right" }}>Value</th>
              <th>Printed as</th>
              <th>Flag</th>
            </tr>
          </thead>
          <tbody>
            {series.flatMap((s) =>
              s.points.map((p) => (
                <tr key={`${s.series_key}-${p.t}`}>
                  <td>{DATE.format(new Date(p.t))}</td>
                  {series.length > 1 && <td>{s.laterality ?? "—"}</td>}
                  <td className="num">
                    {formatValue(p.value)} {displayUnit(s.unit)}
                  </td>
                  <td className="muted small">{s.label_raw_examples[0]}</td>
                  <td>
                    {p.abnormal_flag ? (
                      <span className="pill crit">{p.abnormal_flag}</span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      ) : (
        <LineChart series={series} />
      )}

      <div className="legend">
        {series.length > 1 &&
          series.map((s, index) => (
            <span key={s.series_key}>
              <span
                className="swatch"
                style={{ background: `var(--series-${(index % 3) + 1})` }}
              />
              {s.laterality ?? s.label}
            </span>
          ))}
        {latest && (
          <span className="muted">
            latest {formatValue(latest.value)} {displayUnit(first.unit)} on{" "}
            {DATE.format(new Date(latest.t))}
          </span>
        )}
        {trend !== null && Math.abs(trend) > 0.001 && (
          <span className="muted">
            trend {trend > 0 ? "+" : "−"}
            {formatValue(Math.abs(trend))} {displayUnit(first.unit)}/year
          </span>
        )}
        {first.excluded_points > 0 && (
          <span className="pill warn">
            {first.excluded_points} reading{first.excluded_points > 1 ? "s" : ""} not plotted
            (unconvertible unit)
          </span>
        )}
      </div>
    </section>
  );
}
