import { useEffect, useState } from 'react';
import { Search, Upload, RefreshCw, Library } from 'lucide-react';
import { searchCorpus, getCorpusStats, type CorpusStats } from '../api';

export default function CorpusManager() {
  const [stats, setStats] = useState<CorpusStats | null>(null);
  const [results, setResults] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getCorpusStats().then(setStats).catch(() => {});
    doSearch();
  }, []);

  const doSearch = (q?: string) => {
    setLoading(true);
    const params: Record<string, string | number> = { limit: 20, offset: 0 };
    if (q || searchQuery) params.query = q ?? searchQuery;
    if (sourceFilter) params.source_type = sourceFilter;
    searchCorpus(params)
      .then(r => { setResults(r.results); setTotal(r.total); })
      .finally(() => setLoading(false));
  };

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Corpus Manager</h2>
        <p className="subtitle">Browse and manage the document corpus used for matching</p>
      </div>

      <div className="page-body">
        {/* Stats */}
        {stats && (
          <div className="stats-grid" style={{ marginBottom: '2rem' }}>
            <div className="stat-card">
              <div className="stat-label">Total Documents</div>
              <div className="stat-value accent">{stats.total_documents.toLocaleString()}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Indexed</div>
              <div className="stat-value success">{stats.indexed_count.toLocaleString()}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Full Text</div>
              <div className="stat-value">{stats.full_text_count.toLocaleString()}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">With DOI</div>
              <div className="stat-value">{stats.with_doi.toLocaleString()}</div>
            </div>
          </div>
        )}

        {/* Search */}
        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem' }}>
          <input
            className="input"
            placeholder="Search by title, DOI, or abstract..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && doSearch()}
            style={{ flex: 1 }}
          />
          <select className="select" value={sourceFilter} onChange={e => setSourceFilter(e.target.value)} style={{ width: 180 }}>
            <option value="">All sources</option>
            <option value="user_upload">User Upload</option>
            <option value="wikipedia">Wikipedia</option>
            <option value="openalex">OpenAlex</option>
            <option value="arxiv">arXiv</option>
            <option value="crossref">Crossref</option>
          </select>
          <button className="btn btn-primary" onClick={() => doSearch()}>
            <Search size={16} /> Search
          </button>
        </div>

        {/* Results */}
        {loading ? (
          <div className="loading-overlay"><div className="spinner" /> Searching...</div>
        ) : results.length === 0 ? (
          <div className="empty-state">
            <Library size={48} />
            <h3>No documents found</h3>
            <p>Upload documents or ingest scholarly papers to build your corpus.</p>
          </div>
        ) : (
          <>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
              {total.toLocaleString()} documents found
            </p>
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Source</th>
                    <th>Year</th>
                    <th>DOI</th>
                    <th>Status</th>
                    <th>Full Text</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map(doc => (
                    <tr key={doc.id}>
                      <td style={{ maxWidth: 350, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-primary)', fontWeight: 450 }}>
                        {doc.source_url ? (
                          <a href={doc.source_url} target="_blank" rel="noopener noreferrer">
                            {doc.title || '(untitled)'}
                          </a>
                        ) : (
                          doc.title || '(untitled)'
                        )}
                      </td>
                      <td>
                        <span className="badge badge-neutral">{doc.source_type}</span>
                      </td>
                      <td>{doc.publication_year || '—'}</td>
                      <td style={{ fontSize: '0.75rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                        {doc.doi ? (
                          <a href={`https://doi.org/${doc.doi}`} target="_blank" rel="noopener noreferrer">
                            {doc.doi.length > 30 ? doc.doi.slice(0, 30) + '…' : doc.doi}
                          </a>
                        ) : '—'}
                      </td>
                      <td>
                        <span className={`badge ${doc.fingerprint_status === 'fingerprinted' ? 'badge-success' : doc.fingerprint_status === 'failed' ? 'badge-danger' : 'badge-warning'}`}>
                          {doc.fingerprint_status}
                        </span>
                      </td>
                      <td>
                        {doc.full_text_available ? (
                          <span className="badge badge-success">Yes</span>
                        ) : (
                          <span className="badge badge-neutral">No</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
