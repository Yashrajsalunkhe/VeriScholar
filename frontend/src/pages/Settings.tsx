import { Settings as SettingsIcon, Info } from 'lucide-react';

export default function Settings() {
  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Settings</h2>
        <p className="subtitle">Platform configuration and system information</p>
      </div>

      <div className="page-body">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
          {/* Detection weights */}
          <div className="card" style={{ padding: '1.5rem' }}>
            <h3 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '1.25rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <SettingsIcon size={18} /> Detection Layers
            </h3>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.3rem' }}>
                  <span style={{ fontSize: '0.85rem', fontWeight: 500 }}>Exact Match (Winnowing)</span>
                  <span className="badge badge-success">Active</span>
                </div>
                <div className="progress-bar" style={{ height: 8 }}>
                  <div className="progress-bar-fill" style={{ width: '50%', background: 'var(--success)' }} />
                </div>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Weight: 50% · k=5, w=8</span>
              </div>

              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.3rem' }}>
                  <span style={{ fontSize: '0.85rem', fontWeight: 500 }}>Fuzzy Match (Token/N-gram)</span>
                  <span className="badge badge-success">Active</span>
                </div>
                <div className="progress-bar" style={{ height: 8 }}>
                  <div className="progress-bar-fill" style={{ width: '25%', background: 'var(--warning)' }} />
                </div>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Weight: 25% · Jaccard + Dice + LCS</span>
              </div>

              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.3rem' }}>
                  <span style={{ fontSize: '0.85rem', fontWeight: 500 }}>Semantic Match (Embeddings)</span>
                  <span className="badge badge-info">Optional</span>
                </div>
                <div className="progress-bar" style={{ height: 8 }}>
                  <div className="progress-bar-fill" style={{ width: '25%', background: 'var(--accent-secondary)' }} />
                </div>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Weight: 25% · all-MiniLM-L6-v2 + FAISS</span>
              </div>
            </div>
          </div>

          {/* System info */}
          <div className="card" style={{ padding: '1.5rem' }}>
            <h3 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '1.25rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Info size={18} /> System Information
            </h3>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {[
                ['Engine', 'VeriScholar AI v0.2.0'],
                ['Base', 'Noplag Engine (Apache-2.0)'],
                ['Python', '3.12+'],
                ['Database', 'PostgreSQL 16'],
                ['Pipeline', 'Winnowing → Seed-Extend → Fuzzy → Semantic'],
                ['Scholarly APIs', 'OpenAlex, arXiv, Crossref'],
                ['Embedding Model', 'all-MiniLM-L6-v2 (optional)'],
                ['Vector Index', 'FAISS (optional)'],
              ].map(([label, value]) => (
                <div key={label} style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border-secondary)', paddingBottom: '0.5rem' }}>
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{label}</span>
                  <span style={{ fontSize: '0.8rem', fontWeight: 500 }}>{value}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Data sources */}
          <div className="card" style={{ padding: '1.5rem', gridColumn: '1 / -1' }}>
            <h3 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '1.25rem' }}>
              Scholarly Data Sources
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem' }}>
              {[
                {
                  name: 'OpenAlex',
                  desc: '250M+ scholarly works with metadata and open-access links',
                  status: 'Available',
                  color: 'var(--success)',
                },
                {
                  name: 'arXiv',
                  desc: 'Open-access preprints in STEM fields with PDF downloads',
                  status: 'Available',
                  color: 'var(--success)',
                },
                {
                  name: 'Crossref',
                  desc: 'DOI resolution and publication metadata for 140M+ records',
                  status: 'Available',
                  color: 'var(--success)',
                },
              ].map(src => (
                <div key={src.name} style={{
                  padding: '1rem', background: 'var(--bg-tertiary)',
                  borderRadius: 'var(--radius-sm)',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                    <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{src.name}</span>
                    <span style={{ fontSize: '0.7rem', color: src.color, fontWeight: 600 }}>● {src.status}</span>
                  </div>
                  <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                    {src.desc}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
