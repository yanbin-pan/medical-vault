import { useState } from "react";
import { api, type DocumentRecord } from "../lib/api";
import { useAsync } from "../lib/useAsync";

const DATE = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });

export function Documents({
  tenant,
  onOpen,
}: {
  tenant: string;
  onOpen: (id: string) => void;
}) {
  const [includeSuperseded, setIncludeSuperseded] = useState(false);
  const documents = useAsync(
    () => api.documents(tenant, includeSuperseded),
    [tenant, includeSuperseded],
  );

  const reviewPill = (doc: DocumentRecord) => {
    const status = (doc.review?.status as string) ?? "unreviewed";
    if (doc.superseded_by) return <span className="pill">superseded</span>;
    if (status === "verified") return <span className="pill ok">verified</span>;
    if (status === "corrected") return <span className="pill ok">corrected</span>;
    return <span className="pill warn">unreviewed</span>;
  };

  return (
    <>
      <div className="toolbar">
        <label className="small">
          <input
            type="checkbox"
            checked={includeSuperseded}
            onChange={(e) => setIncludeSuperseded(e.target.checked)}
          />{" "}
          Show corrected-over versions
        </label>
      </div>

      {documents.error && <p className="banner err">{documents.error}</p>}
      {documents.loading && <p className="muted">Loading…</p>}

      {documents.data && (
        <div className="card">
          <table className="data">
            <thead>
              <tr>
                <th>Date</th>
                <th>Type</th>
                <th>Provider</th>
                <th>Language</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {documents.data.map((doc) => (
                <tr key={doc.id} style={{ opacity: doc.superseded_by ? 0.55 : 1 }}>
                  <td>{DATE.format(new Date(doc.captured_at))}</td>
                  <td>{doc.document_type ?? "—"}</td>
                  <td>
                    {doc.provider?.name_en ?? doc.provider?.name_raw ?? "—"}
                    {doc.provider?.name_raw && doc.provider?.name_en && (
                      <div className="muted small">{doc.provider.name_raw}</div>
                    )}
                  </td>
                  <td className="muted">{doc.language ?? "—"}</td>
                  <td>{reviewPill(doc)}</td>
                  <td>
                    <button onClick={() => onOpen(doc.id)}>Open</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {documents.data.length === 0 && <p className="muted">No documents yet.</p>}
        </div>
      )}
    </>
  );
}
