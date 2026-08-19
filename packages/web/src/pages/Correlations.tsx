import { useState } from "react";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { CorrelationMatrix } from "../components/CorrelationMatrix";

export function Correlations({ tenant, subject }: { tenant: string; subject: string }) {
  const [windowDays, setWindowDays] = useState(3);
  const [minPoints, setMinPoints] = useState(4);
  const [method, setMethod] = useState<"pearson" | "spearman">("pearson");

  const correlations = useAsync(
    () => api.correlations(tenant, subject, windowDays, minPoints),
    [tenant, subject, windowDays, minPoints],
  );

  return (
    <>
      <div className="banner">
        Correlations are computed only on visits where <em>both</em> measurements
        were actually taken — never by filling in a value for a day with no test.
        Two things moving together here is not evidence that one causes the other,
        and with a handful of yearly check-ups even a strong-looking coefficient
        can be coincidence.
      </div>

      <div className="toolbar">
        <label className="small muted" htmlFor="window">
          Same visit within
        </label>
        <select
          id="window"
          value={windowDays}
          onChange={(e) => setWindowDays(Number(e.target.value))}
        >
          <option value={1}>1 day</option>
          <option value={3}>3 days</option>
          <option value={14}>2 weeks</option>
          <option value={60}>2 months</option>
        </select>

        <label className="small muted" htmlFor="minpoints">
          Minimum paired visits
        </label>
        <select
          id="minpoints"
          value={minPoints}
          onChange={(e) => setMinPoints(Number(e.target.value))}
        >
          {[3, 4, 5, 6, 8].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>

        <label className="small muted" htmlFor="method">
          Method
        </label>
        <select
          id="method"
          value={method}
          onChange={(e) => setMethod(e.target.value as "pearson" | "spearman")}
        >
          <option value="pearson">Pearson (linear)</option>
          <option value="spearman">Spearman (rank)</option>
        </select>
      </div>

      {correlations.error && <p className="banner err">{correlations.error}</p>}
      {correlations.loading && <p className="muted">Computing…</p>}
      {correlations.data && (
        <div className="card">
          <CorrelationMatrix correlations={correlations.data} method={method} />
        </div>
      )}
    </>
  );
}
