import { useState } from 'react';
import { GraduationCap, Search, Download, RefreshCw } from 'lucide-react';
import { startIngestion, listIngestionJobs, type ScholarlyJob } from '../api';
import { useEffect } from 'react';

export default function ScholarlyIngest() {
  const [source, setSource] = useState('openalex');
  const [query, setQuery] = useState('');
  const [author, setAuthor] = useState('');
  const [fromYear, setFromYear] = useState('');
  const [toYear, setToYear] = useState('');
  const [maxResults, setMaxResults] = useState('100');
  const [downloadFull, setDownloadFull] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState('');
  const [jobs, setJobs] = useState<ScholarlyJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(true);

  const loadJobs = () => {
    setJobsLoading(true);
    listIngestionJobs(10)
      .then(r => setJobs(r.jobs))
      .finally(() => setJobsLoading(false));
  };

  useEffect(() => { loadJobs(); }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query && !author) {
      setMessage('Please provide a search query or author name.');
      return;
    }
    setSubmitting(true);
    setMessage('');
    try {
      const result = await startIngestion({
        source,
        query: query || undefined,
        author: author || undefined,
        from_year: fromYear ? parseInt(fromYear) : undefined,
        to_year: toYear ? parseInt(toYear) : undefined,
        max_results: parseInt(maxResults) || 100,
        download_full_text: downloadFull,
      });
      setMessage(`✓ Job started: ${result.message} (ID: ${result.job_id.slice(0, 8)}…)`);
      setTimeout(loadJobs, 2000);
    } catch (err: any) {
      setMessage(`✗ ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const formatDate = (iso: string | null) => {
    if (!iso) return '—';
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  };

  const statusBadge = (s: string) => {
    const cls = {
      completed: 'badge-success', failed: 'badge-danger', running: 'badge-info',
      pending: 'badge-neutral', cancelled: 'badge-warning',
    }[s] || 'badge-neutral';
    return <span className={`badge ${cls}`}>{s}</span>;
  };

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Scholarly Corpus Ingestion</h2>
        <p className="subtitle">
          Discover and index research papers from OpenAlex, arXiv, and Crossref
        </p>
      </div>

      <div className="page-body">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
          {/* Ingestion form */}
          <div className="card" style={{ padding: '1.5rem' }}>
            <h3 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '1.25rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <GraduationCap size={18} /> New Ingestion Job
            </h3>

            <form onSubmit={handleSubmit}>
              <div className="form-group">
                <label className="form-label">Source</label>
                <select className="select" value={source} onChange={e => setSource(e.target.value)}>
                  <option value="openalex">OpenAlex (250M+ works)</option>
                  <option value="arxiv">arXiv (preprints)</option>
                  <option value="crossref">Crossref (DOI metadata)</option>
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">Search Query</label>
                <input
                  className="input"
                  placeholder="e.g., machine learning, deep neural networks"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label className="form-label">Author</label>
                <input
                  className="input"
                  placeholder="e.g., Geoffrey Hinton"
                  value={author}
                  onChange={e => setAuthor(e.target.value)}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                <div className="form-group">
                  <label className="form-label">From Year</label>
                  <input
                    className="input" type="number"
                    placeholder="2020"
                    value={fromYear}
                    onChange={e => setFromYear(e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">To Year</label>
                  <input
                    className="input" type="number"
                    placeholder="2026"
                    value={toYear}
                    onChange={e => setToYear(e.target.value)}
                  />
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Max Results</label>
                <input
                  className="input" type="number"
                  value={maxResults}
                  onChange={e => setMaxResults(e.target.value)}
                  min={1} max={10000}
                />
              </div>

              <div className="form-group" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <input
                  type="checkbox" id="dl-full"
                  checked={downloadFull}
                  onChange={e => setDownloadFull(e.target.checked)}
                />
                <label htmlFor="dl-full" style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  Download full-text PDFs (when available)
                </label>
              </div>

              {message && (
                <div style={{
                  padding: '0.6rem 0.85rem', borderRadius: 'var(--radius-sm)',
                  fontSize: '0.8rem', marginBottom: '1rem',
                  background: message.startsWith('✓') ? 'var(--success-bg)' : 'var(--danger-bg)',
                  color: message.startsWith('✓') ? 'var(--success)' : 'var(--danger)',
                }}>
                  {message}
                </div>
              )}

              <button className="btn btn-primary" type="submit" disabled={submitting}>
                {submitting ? (
                  <><RefreshCw size={16} style={{ animation: 'spin 1s linear infinite' }} /> Starting...</>
                ) : (
                  <><Search size={16} /> Start Ingestion</>
                )}
              </button>
            </form>
          </div>

          {/* Recent jobs */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
              <h3 style={{ fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>
                Recent Jobs
              </h3>
              <button className="btn btn-ghost btn-sm" onClick={loadJobs}>
                <RefreshCw size={14} /> Refresh
              </button>
            </div>

            {jobsLoading ? (
              <div className="loading-overlay"><div className="spinner" /></div>
            ) : jobs.length === 0 ? (
              <div className="card empty-state" style={{ padding: '2rem' }}>
                <Download size={32} />
                <h3>No ingestion jobs yet</h3>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {jobs.map(job => (
                  <div key={job.id} className="card" style={{ padding: '1rem 1.25rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                      <span style={{ fontWeight: 500, fontSize: '0.85rem', textTransform: 'capitalize' }}>
                        {job.source}
                      </span>
                      {statusBadge(job.status)}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.5rem', fontSize: '0.75rem' }}>
                      <div>
                        <span style={{ color: 'var(--text-muted)' }}>Found </span>
                        <span style={{ fontWeight: 600 }}>{job.total_discovered}</span>
                      </div>
                      <div>
                        <span style={{ color: 'var(--text-muted)' }}>Downloaded </span>
                        <span style={{ fontWeight: 600, color: 'var(--info)' }}>{job.total_downloaded}</span>
                      </div>
                      <div>
                        <span style={{ color: 'var(--text-muted)' }}>Indexed </span>
                        <span style={{ fontWeight: 600, color: 'var(--success)' }}>{job.total_indexed}</span>
                      </div>
                      <div>
                        <span style={{ color: 'var(--text-muted)' }}>Failed </span>
                        <span style={{ fontWeight: 600, color: job.total_failed > 0 ? 'var(--danger)' : 'var(--text-muted)' }}>
                          {job.total_failed}
                        </span>
                      </div>
                    </div>
                    {job.total_discovered > 0 && (
                      <div className="progress-bar" style={{ marginTop: '0.5rem' }}>
                        <div
                          className="progress-bar-fill"
                          style={{
                            width: `${Math.round(((job.total_indexed + job.total_skipped + job.total_failed) / job.total_discovered) * 100)}%`,
                          }}
                        />
                      </div>
                    )}
                    <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.4rem' }}>
                      {formatDate(job.created_at)}
                      {job.error_message && (
                        <span style={{ color: 'var(--danger)', marginLeft: '0.5rem' }}>
                          {job.error_message.slice(0, 100)}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
