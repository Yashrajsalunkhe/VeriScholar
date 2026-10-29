import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileSearch, BookOpen } from 'lucide-react';
import { getCheckStats, getCorpusStats, type CheckStats, type CorpusStats } from '../api';

export default function Dashboard() {
  const navigate = useNavigate();
  const [checkStats, setCheckStats] = useState<CheckStats | null>(null);
  const [corpusStats, setCorpusStats] = useState<CorpusStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      getCheckStats().catch(() => null),
      getCorpusStats().catch(() => null),
    ]).then(([cs, co]) => {
      setCheckStats(cs);
      setCorpusStats(co);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div className="loading-overlay">
        <div className="spinner" />
        Loading dashboard...
      </div>
    );
  }

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Dashboard</h2>
        <p className="subtitle">
          Overview of your plagiarism detection system
        </p>
      </div>

      <div className="page-body">
        {/* Quick actions */}
        <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '2rem' }}>
          <button className="btn btn-primary btn-lg" onClick={() => navigate('/check')}>
            <FileSearch size={18} /> New Check
          </button>
          <button className="btn btn-secondary" onClick={() => navigate('/scholarly')}>
            <BookOpen size={18} /> Ingest Papers
          </button>
        </div>

        {/* Check Statistics */}
        <h3 style={{
          fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase',
          letterSpacing: '0.08em', color: 'var(--text-tertiary)', marginBottom: '0.75rem',
        }}>
          Check Statistics
        </h3>
        <div className="stats-grid" style={{ marginBottom: '2rem' }}>
          <div className="stat-card card-glow">
            <div className="stat-label">Total Checks</div>
            <div className="stat-value accent">
              {checkStats?.total_checks?.toLocaleString() ?? '—'}
            </div>
            <div className="stat-sub">
              {checkStats?.checks_this_month ?? 0} this month
            </div>
          </div>
          <div className="stat-card card-glow">
            <div className="stat-label">Avg. Similarity</div>
            <div className={`stat-value ${
              (checkStats?.avg_similarity_pct ?? 0) > 25 ? 'warning' : 'success'
            }`}>
              {checkStats?.avg_similarity_pct != null
                ? `${checkStats.avg_similarity_pct.toFixed(1)}%`
                : '—'}
            </div>
            <div className="stat-sub">
              across {checkStats?.avg_similarity_window ?? 0} checks
            </div>
          </div>
          <div className="stat-card card-glow">
            <div className="stat-label">Words Analyzed</div>
            <div className="stat-value">
              {checkStats?.total_words
                ? (checkStats.total_words >= 1_000_000
                    ? `${(checkStats.total_words / 1_000_000).toFixed(1)}M`
                    : checkStats.total_words.toLocaleString())
                : '—'}
            </div>
            <div className="stat-sub">total across all checks</div>
          </div>
          <div className="stat-card card-glow">
            <div className="stat-label">Sources Matched</div>
            <div className="stat-value">
              {checkStats?.total_sources?.toLocaleString() ?? '—'}
            </div>
            <div className="stat-sub">unique source documents</div>
          </div>
        </div>

        {/* Corpus Statistics */}
        <h3 style={{
          fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase',
          letterSpacing: '0.08em', color: 'var(--text-tertiary)', marginBottom: '0.75rem',
        }}>
          Corpus Coverage
        </h3>
        <div className="stats-grid">
          <div className="stat-card card-glow">
            <div className="stat-label">Total Documents</div>
            <div className="stat-value accent">
              {corpusStats?.total_documents?.toLocaleString() ?? '—'}
            </div>
            <div className="stat-sub">
              {corpusStats?.indexed_count?.toLocaleString() ?? 0} indexed
            </div>
          </div>
          <div className="stat-card card-glow">
            <div className="stat-label">Full Text</div>
            <div className="stat-value success">
              {corpusStats?.full_text_count?.toLocaleString() ?? '—'}
            </div>
            <div className="stat-sub">
              {corpusStats?.abstract_only_count ?? 0} abstract only
            </div>
          </div>
          <div className="stat-card card-glow">
            <div className="stat-label">With DOI</div>
            <div className="stat-value">
              {corpusStats?.with_doi?.toLocaleString() ?? '—'}
            </div>
            <div className="stat-sub">scholarly provenance</div>
          </div>
          <div className="stat-card card-glow">
            <div className="stat-label">Year Coverage</div>
            <div className="stat-value" style={{ fontSize: '1.1rem' }}>
              {corpusStats?.earliest_year && corpusStats?.latest_year
                ? `${corpusStats.earliest_year}–${corpusStats.latest_year}`
                : '—'}
            </div>
            <div className="stat-sub">publication year range</div>
          </div>
        </div>
      </div>
    </div>
  );
}
