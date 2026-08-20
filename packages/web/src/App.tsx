import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { useAsync } from "./lib/useAsync";
import { Dashboard } from "./pages/Dashboard";
import { Correlations } from "./pages/Correlations";
import { Documents } from "./pages/Documents";
import { DocumentDetail } from "./pages/DocumentDetail";
import { Upload } from "./pages/Upload";

type Tab = "charts" | "correlations" | "documents" | "upload";

export function App() {
  const tenants = useAsync(() => api.tenants(), []);
  const [tenant, setTenant] = useState<string | null>(null);
  const [subject, setSubject] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("charts");
  const [openDocument, setOpenDocument] = useState<string | null>(null);

  useEffect(() => {
    if (!tenant && tenants.data?.length) setTenant(tenants.data[0].id);
  }, [tenants.data, tenant]);

  const subjects = useAsync(
    () => (tenant ? api.subjects(tenant) : Promise.resolve([])),
    [tenant],
  );

  useEffect(() => {
    if (subjects.data?.length && !subjects.data.some((s) => s.id === subject)) {
      setSubject(subjects.data[0].id);
    }
  }, [subjects.data, subject]);

  if (tenants.error) {
    return (
      <div className="app">
        <p className="banner err">
          Could not sign you in: {tenants.error}. This service expects to sit
          behind Cloudflare Access.
        </p>
      </div>
    );
  }

  if (!tenant || !subject) {
    return (
      <div className="app">
        <p className="muted">{tenants.loading ? "Loading…" : "No records are shared with you."}</p>
      </div>
    );
  }

  const currentSubject = subjects.data?.find((s) => s.id === subject);

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <h1>Medical Vault</h1>
          <div className="sub">
            {currentSubject?.display_name ?? subject}
            {currentSubject?.birth_date && ` · born ${currentSubject.birth_date}`}
          </div>
        </div>

        <div className="toolbar" style={{ margin: 0 }}>
          {(tenants.data?.length ?? 0) > 1 && (
            <select value={tenant} onChange={(e) => setTenant(e.target.value)}>
              {tenants.data?.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.display_name}
                </option>
              ))}
            </select>
          )}
          {(subjects.data?.length ?? 0) > 1 && (
            <select value={subject} onChange={(e) => setSubject(e.target.value)}>
              {subjects.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.display_name}
                </option>
              ))}
            </select>
          )}
        </div>

        <nav className="tabs">
          {(
            [
              ["charts", "Charts"],
              ["correlations", "Correlations"],
              ["documents", "Documents"],
              ["upload", "Add a result"],
            ] as [Tab, string][]
          ).map(([key, label]) => (
            <a
              key={key}
              href={`#${key}`}
              className={tab === key && !openDocument ? "active" : ""}
              onClick={(e) => {
                e.preventDefault();
                setOpenDocument(null);
                setTab(key);
              }}
            >
              {label}
            </a>
          ))}
        </nav>
      </header>

      {openDocument ? (
        <DocumentDetail
          tenant={tenant}
          documentId={openDocument}
          onBack={() => setOpenDocument(null)}
        />
      ) : tab === "charts" ? (
        <Dashboard tenant={tenant} subject={subject} />
      ) : tab === "correlations" ? (
        <Correlations tenant={tenant} subject={subject} />
      ) : tab === "documents" ? (
        <Documents tenant={tenant} onOpen={setOpenDocument} />
      ) : (
        <Upload tenant={tenant} subject={subject} onUploaded={setOpenDocument} />
      )}
    </div>
  );
}
