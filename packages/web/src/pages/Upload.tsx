import { useState } from "react";
import { api } from "../lib/api";

export function Upload({
  tenant,
  subject,
  onUploaded,
}: {
  tenant: string;
  subject: string;
  onUploaded: (documentId: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [capturedAt, setCapturedAt] = useState("");
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setWarnings([]);
    try {
      const result = await api.upload(tenant, subject, file, capturedAt || undefined, hint || undefined);
      setWarnings([...result.warnings, ...result.unmapped_labels.map((l) => `New measurement: ${l}`)]);
      onUploaded(result.document.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit} style={{ maxWidth: 620 }}>
      <h3 className="chart-title">Add a result</h3>
      <p className="chart-sub" style={{ marginBottom: 16 }}>
        A photograph of a printed report in any language. The reading is checked
        by you before it counts as verified.
      </p>

      <div style={{ display: "grid", gap: 12 }}>
        <label>
          <div className="small muted">Photograph or PDF</div>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            required
          />
        </label>

        <label>
          <div className="small muted">
            Examination date — only needed if the document does not show one
          </div>
          <input type="date" value={capturedAt} onChange={(e) => setCapturedAt(e.target.value)} />
        </label>

        <label>
          <div className="small muted">Anything that would help read it (optional)</div>
          <input
            type="text"
            value={hint}
            onChange={(e) => setHint(e.target.value)}
            placeholder="e.g. page 2 of 3, the date is on the first page"
          />
        </label>

        <div>
          <button className="primary" type="submit" disabled={!file || busy}>
            {busy ? "Reading the document…" : "Read and file it"}
          </button>
        </div>
      </div>

      {error && (
        <p className="banner err" style={{ marginTop: 16 }}>
          {error}
        </p>
      )}
      {warnings.length > 0 && (
        <div className="banner warn" style={{ marginTop: 16 }}>
          <strong>Worth checking:</strong>
          <ul style={{ margin: "6px 0 0 16px", padding: 0 }}>
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </form>
  );
}
