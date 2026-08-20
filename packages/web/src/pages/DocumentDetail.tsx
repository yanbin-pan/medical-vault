import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { displayUnit } from "../lib/units";

const DATE = new Intl.DateTimeFormat(undefined, {
  year: "numeric", month: "long", day: "numeric",
});

/**
 * One document: the photograph beside the numbers read from it.
 *
 * Showing both together is what makes an AI extraction checkable rather than
 * something to be taken on faith, so the original image is not tucked away
 * behind a link.
 */
export function DocumentDetail({
  tenant,
  documentId,
  onBack,
}: {
  tenant: string;
  documentId: string;
  onBack: () => void;
}) {
  const doc = useAsync(() => api.document(tenant, documentId), [tenant, documentId]);
  const rows = useAsync(
    () => api.documentObservations(tenant, documentId),
    [tenant, documentId],
  );

  const markReviewed = async () => {
    await api.review(tenant, documentId, "verified");
    doc.reload();
  };

  if (doc.error) return <p className="banner err">{doc.error}</p>;
  if (!doc.data) return <p className="muted">Loading…</p>;

  const extraction = doc.data.extraction ?? {};
  const status = (doc.data.review?.status as string) ?? "unreviewed";

  return (
    <>
      <div className="toolbar">
        <button onClick={onBack}>← All documents</button>
        {status === "unreviewed" && (
          <button className="primary" onClick={markReviewed}>
            Mark as checked against the paper
          </button>
        )}
        {status !== "unreviewed" && <span className="pill ok">{status}</span>}
      </div>

      {doc.data.superseded_by && (
        <p className="banner warn">
          A corrected version of this document exists. These readings are kept for
          the record but are excluded from charts.
        </p>
      )}

      <div className="doc-layout">
        <div className="card">
          <img
            src={api.originalUrl(tenant, documentId)}
            alt="The original document as photographed"
          />
          <dl className="small" style={{ marginBottom: 0 }}>
            <div>
              <dt className="muted">Examined</dt>
              <dd>{DATE.format(new Date(doc.data.captured_at))}</dd>
            </div>
            <div>
              <dt className="muted">Provider</dt>
              <dd>
                {doc.data.provider?.name_raw ?? "—"}
                {doc.data.provider?.name_en && (
                  <div className="muted">{doc.data.provider.name_en}</div>
                )}
              </dd>
            </div>
            <div>
              <dt className="muted">Read by</dt>
              <dd>
                {(extraction.model as string) ?? "manual entry"}
                <div className="muted">prompt {(extraction.prompt_version as string) ?? "—"}</div>
              </dd>
            </div>
          </dl>
        </div>

        <div style={{ display: "grid", gap: 16 }}>
          {doc.data.narrative.length > 0 && (
            <section className="card">
              <h3 className="chart-title">Findings</h3>
              {doc.data.narrative.map((n, index) => (
                <div key={index} style={{ marginTop: 10 }}>
                  <div className="muted small">{n.section}</div>
                  <div>{n.text_en ?? n.text_raw}</div>
                  {n.text_en && <div className="muted small">{n.text_raw}</div>}
                </div>
              ))}
            </section>
          )}

          <section className="card">
            <h3 className="chart-title">Measurements read from this document</h3>
            {rows.data && (
              <table className="data">
                <thead>
                  <tr>
                    <th>As printed</th>
                    <th>Meaning</th>
                    <th style={{ textAlign: "right" }}>Value</th>
                    <th style={{ textAlign: "right" }}>Comparable</th>
                    <th>Range</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.data.map((row) => (
                    <tr key={row.id}>
                      <td>{row.label_raw}</td>
                      <td>
                        {row.label_en ?? <span className="muted">—</span>}
                        {!row.is_mapped && (
                          <span className="pill warn" style={{ marginLeft: 6 }}>
                            new
                          </span>
                        )}
                      </td>
                      <td className="num">
                        {row.value_num ?? row.value_text ?? "—"}{" "}
                        <span className="muted">{row.unit_raw ?? ""}</span>
                      </td>
                      <td className="num">
                        {row.canonical_value !== null ? (
                          <>
                            {Number(row.canonical_value.toPrecision(4))}{" "}
                            <span className="muted">{displayUnit(row.canonical_unit)}</span>
                          </>
                        ) : (
                          <span className="muted" title={row.normalisation_notes.join("; ")}>
                            —
                          </span>
                        )}
                      </td>
                      <td className="muted small">
                        {row.reference_low !== null && row.reference_high !== null
                          ? `${row.reference_low}–${row.reference_high}`
                          : "—"}
                        {row.abnormal_flag && (
                          <span className="pill crit" style={{ marginLeft: 6 }}>
                            {row.abnormal_flag}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      </div>
    </>
  );
}
