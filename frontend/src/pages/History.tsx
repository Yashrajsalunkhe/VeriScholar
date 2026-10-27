import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Trash2, Eye, Clock } from 'lucide-react';
import { listChecks, deleteCheck, type CheckListItem } from '../api';

export default function History() {
  const navigate = useNavigate();
  const [checks, setChecks] = useState<CheckListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const limit = 20;

  const load = () => {
    setLoading(true);
    listChecks(limit, page * limit)
      .then(r => { setChecks(r.checks); setTotal(r.total); })
      .finally(() => setLoading(false));
  };

  useEffect(load, [page]);

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this check?')) return;
    await deleteCheck(id);
    load();
  };

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
      + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  };

  const statusBadge = (status: string) => {
    const cls = { complete: 'badge-success', failed: 'badge-danger', running: 'badge-info', pending: 'badge-neutral' }[status] || 'badge-neutral';
    return <span className={`badge ${cls}`}>{status}</span>;
  };

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Check History</h2>
        <p className="subtitle">{total} total checks</p>
      </div>

      <div className="page-body">
        {loading ? (
          <div className="loading-overlay"><div className="spinner" /> Loading...</div>
        ) : checks.length === 0 ? (
          <div className="empty-state">
            <Clock size={48} />
            <h3>No checks yet</h3>
            <p>Run your first plagiarism check to see results here.</p>
          </div>
        ) : (
          <>
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Status</th>
                    <th>Similarity</th>
                    <th>Sources</th>
                    <th>Length</th>
                    <th>Date</th>
                    <th style={{ width: 80 }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {checks.map(c => (
                    <tr key={c.id} style={{ cursor: 'pointer' }}
                      onClick={() => c.status === 'complete' && navigate(`/report/${c.id}`)}
                    >
                      <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-primary)', fontWeight: 450 }}>
                        {c.title || c.query_text_preview?.slice(0, 60) || 'Untitled'}
                      </td>
                      <td>{statusBadge(c.status)}</td>
                      <td style={{
                        fontWeight: 600,
                        color: c.overall_similarity_pct != null
                          ? (c.overall_similarity_pct >= 25 ? 'var(--danger)' : c.overall_similarity_pct >= 10 ? 'var(--warning)' : 'var(--success)')
                          : 'var(--text-muted)',
                      }}>
                        {c.overall_similarity_pct != null ? `${c.overall_similarity_pct.toFixed(1)}%` : '—'}
                      </td>
                      <td>{c.source_count ?? '—'}</td>
                      <td style={{ fontSize: '0.8rem' }}>
                        {c.word_count?.toLocaleString() ?? '—'} words
                      </td>
                      <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                        {formatDate(c.created_at)}
                      </td>
                      <td>
                        <div style={{ display: 'flex', gap: '0.25rem' }}>
                          {c.status === 'complete' && (
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={e => { e.stopPropagation(); navigate(`/report/${c.id}`); }}
                              title="View Report"
                            >
                              <Eye size={14} />
                            </button>
                          )}
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={e => { e.stopPropagation(); handleDelete(c.id); }}
                            title="Delete"
                            style={{ color: 'var(--danger)' }}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {total > limit && (
              <div style={{ display: 'flex', justifyContent: 'center', gap: '0.5rem', marginTop: '1.5rem' }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}>
                  Previous
                </button>
                <span style={{ padding: '0.4rem 0.85rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Page {page + 1} of {Math.ceil(total / limit)}
                </span>
                <button className="btn btn-ghost btn-sm" onClick={() => setPage(p => p + 1)} disabled={(page + 1) * limit >= total}>
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
