import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, ExternalLink, FileText, Layers } from 'lucide-react';
import { getCheckReport, type CheckReport } from '../api';
import ScoreRing from '../components/ScoreRing';
import HighlightedText from '../components/HighlightedText';

export default function Report() {
  const { id } = useParams<{ id: string }>();
  const [report, setReport] = useState<CheckReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!id) return;
    getCheckReport(id)
      .then(setReport)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <div className="loading-overlay">
        <div className="spinner" />
        Loading report...
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="page-body">
        <Link to="/history" className="btn btn-ghost" style={{ marginBottom: '1rem' }}>
          <ArrowLeft size={16} /> Back to history
        </Link>
        <div className="card" style={{ padding: '2rem', color: 'var(--danger)' }}>
          {error || 'Report not found'}
        </div>
      </div>
    );
  }

  return (
    <div className="fade-in">
      <div className="page-header">
        <Link to="/history" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8rem', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
          <ArrowLeft size={14} /> Check History
        </Link>
        <h2>{report.title || 'Untitled Check'}</h2>
        <p className="subtitle">
          {report.word_count?.toLocaleString()} words · {report.sources.length} source{report.sources.length !== 1 ? 's' : ''} matched
          {report.duration_seconds != null && ` · ${report.duration_seconds}s`}
          {report.corpus_sources != null && ` · corpus of ${report.corpus_sources.toLocaleString()} documents`}
        </p>
      </div>

      <div className="page-body">
        {/* Score + Summary */}
        <div style={{ display: 'flex', gap: '2rem', alignItems: 'flex-start', marginBottom: '2rem', flexWrap: 'wrap' }}>
          <ScoreRing value={report.overall_similarity_pct} />

          <div style={{ flex: 1, minWidth: '240px' }}>
            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))' }}>
              <div className="stat-card">
                <div className="stat-label">Matched Characters</div>
                <div className="stat-value" style={{ fontSize: '1.25rem' }}>
                  {report.total_matched_chars.toLocaleString()}
                </div>
                <div className="stat-sub">of {report.query_text_length.toLocaleString()} total</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Coverage</div>
                <div className="stat-value" style={{ fontSize: '1.25rem' }}>
                  {report.checked_chunks}/{report.total_chunks}
                </div>
                <div className="stat-sub">chunks checked ({report.coverage})</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Unique Passages</div>
                <div className="stat-value" style={{ fontSize: '1.25rem' }}>
                  {report.unique_passages.length}
                </div>
                <div className="stat-sub">matched regions</div>
              </div>
            </div>
          </div>
        </div>

        {/* Highlighted text */}
        <h3 style={{
          fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase',
          letterSpacing: '0.08em', color: 'var(--text-tertiary)', marginBottom: '0.75rem',
        }}>
          <FileText size={14} style={{ verticalAlign: '-2px', marginRight: '0.4rem' }} />
          Submitted Text — matched regions highlighted
        </h3>
        {report.query_text && (
          <HighlightedText text={report.query_text} passages={report.unique_passages} />
        )}

        {/* Matched sources */}
        <h3 style={{
          fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase',
          letterSpacing: '0.08em', color: 'var(--text-tertiary)', margin: '2rem 0 0.75rem',
        }}>
          <Layers size={14} style={{ verticalAlign: '-2px', marginRight: '0.4rem' }} />
          Matched Sources ({report.sources.length})
        </h3>

        {report.sources.length === 0 ? (
          <div className="card" style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
            No matches found — the text appears to be original.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {report.sources.map((src, i) => (
              <div key={i} className="card" style={{
                display: 'flex', alignItems: 'center', gap: '1rem',
                justifyContent: 'space-between', padding: '1rem 1.25rem',
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: '0.9rem' }}>
                    {src.source_url ? (
                      <a href={src.source_url} target="_blank" rel="noopener noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                        {src.source_filename || src.source_url}
                        <ExternalLink size={12} />
                      </a>
                    ) : (
                      src.source_filename || src.source_document_id
                    )}
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                    {src.passages.length} passage{src.passages.length !== 1 ? 's' : ''} · {src.matched_chars.toLocaleString()} chars matched
                  </div>
                  {/* Passage previews */}
                  {src.passages.slice(0, 2).map((p, j) => (
                    p.overlap_text_preview && (
                      <div key={j} style={{
                        marginTop: '0.5rem', padding: '0.5rem 0.75rem',
                        background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)',
                        fontSize: '0.8rem', color: 'var(--text-secondary)',
                        borderLeft: '3px solid var(--danger)',
                      }}>
                        "{p.overlap_text_preview}"
                      </div>
                    )
                  ))}
                </div>
                <div style={{
                  textAlign: 'right', flexShrink: 0,
                }}>
                  <div style={{
                    fontSize: '1.5rem', fontWeight: 700,
                    color: src.similarity_pct >= 25 ? 'var(--danger)' : src.similarity_pct >= 10 ? 'var(--warning)' : 'var(--text-primary)',
                  }}>
                    {src.similarity_pct.toFixed(1)}%
                  </div>
                  <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
                    similarity
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
