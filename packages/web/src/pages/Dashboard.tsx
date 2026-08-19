import { useMemo, useState } from "react";
import { api, type TimeSeries } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { categoryLabel, groupForCharts } from "../lib/groupSeries";
import { SeriesCard } from "../components/SeriesCard";
import { StatTile } from "../components/StatTile";

const DATE = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short" });

export function Dashboard({ tenant, subject }: { tenant: string; subject: string }) {
  const summary = useAsync(() => api.summary(tenant, subject), [tenant, subject]);
  const series = useAsync(() => api.series(tenant, subject), [tenant, subject]);
  const [category, setCategory] = useState<string>("all");

  const categories = useMemo(() => {
    const found = new Set((series.data ?? []).map((s) => s.category));
    return ["all", ...[...found].sort()];
  }, [series.data]);

  const groups = useMemo(() => {
    const filtered = (series.data ?? []).filter(
      (s: TimeSeries) => category === "all" || s.category === category,
    );
    return groupForCharts(filtered);
  }, [series.data, category]);

  if (series.error) return <p className="banner err">Could not load charts: {series.error}</p>;

  return (
    <>
      {summary.data && (
        <div className="grid tiles" style={{ marginBottom: 24 }}>
          <StatTile label="Measurements" value={summary.data.observation_count} />
          <StatTile label="Documents" value={summary.data.document_count} />
          <StatTile label="Tracked over time" value={summary.data.series_count} />
          <StatTile
            label="Span"
            value={
              summary.data.first_record
                ? `${DATE.format(new Date(summary.data.first_record))} –`
                : "—"
            }
            note={
              summary.data.last_record
                ? DATE.format(new Date(summary.data.last_record))
                : undefined
            }
          />
          <StatTile
            label="Awaiting review"
            value={summary.data.needs_review}
            note={summary.data.needs_review > 0 ? "check the paper" : "all checked"}
            tone={summary.data.needs_review > 0 ? "warn" : "ok"}
          />
          {summary.data.unmapped_count > 0 && (
            <StatTile
              label="Not in catalogue"
              value={summary.data.unmapped_count}
              note="stored, unmapped"
              tone="warn"
            />
          )}
        </div>
      )}

      <div className="toolbar">
        <label htmlFor="category" className="small muted">
          Group
        </label>
        <select id="category" value={category} onChange={(e) => setCategory(e.target.value)}>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c === "all" ? "Everything" : categoryLabel(c)}
            </option>
          ))}
        </select>
        <span className="small muted">
          {groups.length} chart{groups.length === 1 ? "" : "s"}
        </span>
      </div>

      {series.loading && <p className="muted">Loading…</p>}
      {!series.loading && groups.length === 0 && (
        <p className="banner">
          Nothing here yet. Upload a photograph of a blood test or scan report to get started.
        </p>
      )}

      <div className="grid charts">
        {groups.map((group) => (
          <SeriesCard key={group[0].series_key} series={group} />
        ))}
      </div>
    </>
  );
}
