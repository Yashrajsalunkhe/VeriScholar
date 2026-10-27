const API_BASE = import.meta.env.VITE_API_URL || '';

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options?.headers || {}),
    },
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || resp.statusText);
  }
  if (resp.status === 204) return {} as T;
  return resp.json();
}

/* ── Check types ──────────────────────────────────────────────── */
export interface CheckCreateResponse {
  check_id: string;
  status_url: string;
  progress_url: string;
  report_url: string;
}

export interface CheckStatus {
  id: string;
  status: 'pending' | 'running' | 'complete' | 'failed';
  stage: string | null;
  title: string | null;
  query_text_length: number;
  total_matched_chars: number | null;
  overall_similarity_pct: number | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface CheckListItem {
  id: string;
  status: string;
  title: string | null;
  query_text_length: number;
  word_count: number | null;
  source_count: number | null;
  query_text_preview: string | null;
  total_matched_chars: number | null;
  overall_similarity_pct: number | null;
  created_at: string;
  completed_at: string | null;
}

export interface AlignedPassage {
  query_chunk_id: string;
  candidate_chunk_id: string;
  query_start: number;
  query_end: number;
  candidate_start: number;
  candidate_end: number;
  score: number;
  match_type: string;
  overlap_text_preview: string | null;
}

export interface MatchedSource {
  source_document_id: string;
  matched_chars: number;
  similarity_pct: number;
  passages: AlignedPassage[];
  source_filename: string | null;
  source_url: string | null;
}

export interface CheckReport {
  title: string | null;
  query_text_length: number;
  word_count: number | null;
  query_text: string | null;
  total_matched_chars: number;
  overall_similarity_pct: number;
  sources: MatchedSource[];
  unique_passages: [number, number][];
  coverage: string;
  coverage_reason: string | null;
  checked_chunks: number;
  total_chunks: number;
  duration_seconds: number | null;
  corpus_sources: number | null;
}

export interface CheckStats {
  total_checks: number;
  checks_this_month: number;
  avg_similarity_pct: number | null;
  avg_similarity_window: number;
  total_words: number;
  total_sources: number;
}

/* ── Scholarly types ──────────────────────────────────────────── */
export interface CorpusStats {
  total_documents: number;
  full_text_count: number;
  abstract_only_count: number;
  metadata_only_count: number;
  source_count: number;
  indexed_count: number;
  pending_count: number;
  failed_count: number;
  with_doi: number;
  earliest_year: number | null;
  latest_year: number | null;
}

export interface ScholarlyJob {
  id: string;
  source: string;
  status: string;
  total_discovered: number;
  total_downloaded: number;
  total_indexed: number;
  total_skipped: number;
  total_failed: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

/* ── API functions ────────────────────────────────────────────── */

// Checks
export const createCheck = (query_text: string, language = 'en') =>
  request<CheckCreateResponse>('/v1/checks', {
    method: 'POST',
    body: JSON.stringify({ query_text, language }),
  });

export const uploadCheck = async (file: File, language = 'en') => {
  const form = new FormData();
  form.append('file', file);
  form.append('language', language);
  const resp = await fetch(`${API_BASE}/v1/checks/upload`, {
    method: 'POST',
    body: form,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || resp.statusText);
  }
  return resp.json() as Promise<CheckCreateResponse>;
};

export const getCheckStatus = (id: string) =>
  request<CheckStatus>(`/v1/checks/${id}`);

export const getCheckReport = (id: string) =>
  request<CheckReport>(`/v1/checks/${id}/report`);

export const listChecks = (limit = 20, offset = 0) =>
  request<{ checks: CheckListItem[]; total: number }>(
    `/v1/checks?limit=${limit}&offset=${offset}`,
  );

export const deleteCheck = (id: string) =>
  request<void>(`/v1/checks/${id}`, { method: 'DELETE' });

export const getCheckStats = () =>
  request<CheckStats>('/v1/checks/stats');

// Scholarly
export const getCorpusStats = () =>
  request<CorpusStats>('/v1/scholarly/stats');

export const startIngestion = (params: {
  source: string;
  query?: string;
  doi?: string;
  author?: string;
  topic?: string;
  from_year?: number;
  to_year?: number;
  max_results?: number;
  download_full_text?: boolean;
}) =>
  request<{ job_id: string; status: string; message: string }>(
    '/v1/scholarly/ingest',
    { method: 'POST', body: JSON.stringify(params) },
  );

export const listIngestionJobs = (limit = 20, offset = 0) =>
  request<{ jobs: ScholarlyJob[]; total: number }>(
    `/v1/scholarly/jobs?limit=${limit}&offset=${offset}`,
  );

export const getIngestionJob = (id: string) =>
  request<ScholarlyJob>(`/v1/scholarly/jobs/${id}`);

export const searchCorpus = (params: Record<string, string | number>) => {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') qs.set(k, String(v));
  }
  return request<{ results: any[]; total: number }>(
    `/v1/scholarly/search?${qs}`,
  );
};
