/** Typed client for the Medical Vault API. */

export interface Tenant {
  id: string;
  display_name: string;
  role: string;
}

export interface Subject {
  id: string;
  tenant_id: string;
  display_name: string;
  birth_date: string | null;
  sex_at_birth: string | null;
}

export interface SeriesPoint {
  t: string;
  value: number;
  document_id: string;
  abnormal_flag: string | null;
  reference_low: number | null;
  reference_high: number | null;
  review_status: string;
  confidence: number | null;
}

export interface TimeSeries {
  series_key: string;
  analyte_code: string;
  label: string;
  label_raw_examples: string[];
  unit: string | null;
  category: string;
  body_site: string | null;
  laterality: string | null;
  is_mapped: boolean;
  higher_is_worse: boolean | null;
  trend_per_year: number | null;
  excluded_points: number;
  points: SeriesPoint[];
}

export interface Correlation {
  series_a: string;
  series_b: string;
  label_a: string;
  label_b: string;
  n: number;
  pearson: number | null;
  spearman: number | null;
}

export interface Summary {
  observation_count: number;
  document_count: number;
  series_count: number;
  unmapped_count: number;
  needs_review: number;
  first_record: string | null;
  last_record: string | null;
  abnormal_latest: string[];
}

export interface DocumentRecord {
  id: string;
  tenant_id: string;
  subject_id: string;
  captured_at: string;
  recorded_at: string;
  document_type: string | null;
  language: string | null;
  supersedes: string | null;
  superseded_by: string | null;
  provider: Record<string, string | null> | null;
  source: Record<string, unknown>;
  extraction: Record<string, unknown> | null;
  review: Record<string, unknown> | null;
  narrative: { section: string; text_raw: string; text_en: string | null }[];
  tags: string[];
  notes: string | null;
}

export interface ObservationRow {
  id: string;
  label_raw: string;
  label_en: string | null;
  analyte_code: string;
  is_mapped: boolean;
  value_num: number | null;
  value_text: string | null;
  unit_raw: string | null;
  canonical_value: number | null;
  canonical_unit: string | null;
  abnormal_flag: string | null;
  reference_low: number | null;
  reference_high: number | null;
  body_site: string | null;
  laterality: string | null;
  confidence: number | null;
  source_context: string | null;
  normalisation_notes: string[];
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      /* the body was not JSON; the status text will do */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  tenants: () => request<Tenant[]>("/tenants"),

  subjects: (tenant: string) => request<Subject[]>(`/tenants/${tenant}/subjects`),

  summary: (tenant: string, subject: string) =>
    request<Summary>(`/tenants/${tenant}/subjects/${subject}/summary`),

  series: (tenant: string, subject: string) =>
    request<TimeSeries[]>(`/tenants/${tenant}/subjects/${subject}/series`),

  correlations: (tenant: string, subject: string, windowDays = 3, minPoints = 4) =>
    request<Correlation[]>(
      `/tenants/${tenant}/subjects/${subject}/correlations` +
        `?window_days=${windowDays}&min_points=${minPoints}`,
    ),

  documents: (tenant: string, includeSuperseded = false) =>
    request<DocumentRecord[]>(
      `/tenants/${tenant}/documents?include_superseded=${includeSuperseded}`,
    ),

  document: (tenant: string, id: string) =>
    request<DocumentRecord>(`/tenants/${tenant}/documents/${id}`),

  documentObservations: (tenant: string, id: string) =>
    request<ObservationRow[]>(`/tenants/${tenant}/documents/${id}/observations`),

  originalUrl: (tenant: string, id: string) =>
    `/api/tenants/${tenant}/documents/${id}/original`,

  review: (tenant: string, id: string, status: string, note?: string) =>
    request<DocumentRecord>(`/tenants/${tenant}/documents/${id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, note }),
    }),

  upload: async (
    tenant: string,
    subject: string,
    file: File,
    capturedAt?: string,
    hint?: string,
  ) => {
    const form = new FormData();
    form.append("file", file);
    if (capturedAt) form.append("captured_at", capturedAt);
    if (hint) form.append("hint", hint);
    return request<{
      document: DocumentRecord;
      warnings: string[];
      unmapped_labels: string[];
    }>(`/tenants/${tenant}/subjects/${subject}/documents`, {
      method: "POST",
      body: form,
    });
  },
};
