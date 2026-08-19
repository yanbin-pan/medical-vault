import type { TimeSeries } from "./api";

/**
 * Group series that belong on one chart.
 *
 * A left and a right kidney are separate series — they must never be averaged —
 * but they belong on the same axes, because the comparison between them is the
 * interesting part. Everything else stands alone.
 */
export function groupForCharts(series: TimeSeries[]): TimeSeries[][] {
  const groups = new Map<string, TimeSeries[]>();
  for (const s of series) {
    const key = `${s.analyte_code}|${s.body_site ?? ""}`;
    const bucket = groups.get(key);
    if (bucket) bucket.push(s);
    else groups.set(key, [s]);
  }
  return [...groups.values()].map((group) =>
    // left before right, so the colours stay stable across renders.
    group.sort((a, b) => (a.laterality ?? "").localeCompare(b.laterality ?? "")),
  );
}

export function categoryLabel(category: string): string {
  return category
    .split("-")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}
