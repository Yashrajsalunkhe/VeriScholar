import { useState, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Upload, FileText, Loader, AlertCircle } from 'lucide-react';
import { createCheck, uploadCheck, getCheckStatus } from '../api';

export default function NewCheck() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<'text' | 'upload'>('text');
  const [text, setText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [running, setRunning] = useState(false);
  const [stage, setStage] = useState('');
  const [error, setError] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const handleSubmit = useCallback(async () => {
    setError('');
    setRunning(true);
    setStage('submitting…');

    try {
      let result;
      if (mode === 'upload' && file) {
        result = await uploadCheck(file);
      } else if (mode === 'text' && text.trim()) {
        result = await createCheck(text);
      } else {
        throw new Error('Please provide text or upload a document.');
      }

      // Poll for completion
      const checkId = result.check_id;
      setStage('processing…');

      for (let i = 0; i < 300; i++) {
        await new Promise(r => setTimeout(r, 1000));
        const status = await getCheckStatus(checkId);
        if (status.status === 'complete') {
          navigate(`/report/${checkId}`);
          return;
        }
        if (status.status === 'failed') {
          throw new Error(status.error_message || 'Check failed');
        }
        setStage(status.stage ? `${status.stage}…` : 'processing…');
      }
      throw new Error('Check timed out');
    } catch (err: any) {
      setError(err.message || 'Something went wrong');
    } finally {
      setRunning(false);
      setStage('');
    }
  }, [mode, text, file, navigate]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (f) { setFile(f); setMode('upload'); }
  }, []);

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>New Plagiarism Check</h2>
        <p className="subtitle">
          Paste text or upload a document (PDF, DOCX, TXT) to check against the corpus
        </p>
      </div>

      <div className="page-body">
        {/* Mode tabs */}
        <div style={{
          display: 'flex', gap: '0.5rem', marginBottom: '1.5rem',
        }}>
          <button
            className={`btn ${mode === 'text' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setMode('text')}
          >
            <FileText size={16} /> Paste Text
          </button>
          <button
            className={`btn ${mode === 'upload' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setMode('upload')}
          >
            <Upload size={16} /> Upload Document
          </button>
        </div>

        {/* Text input */}
        {mode === 'text' && (
          <div className="form-group">
            <textarea
              className="textarea"
              placeholder="Paste your text here to check for plagiarism…&#10;&#10;Include at least a few sentences for meaningful results."
              value={text}
              onChange={e => setText(e.target.value)}
              style={{ minHeight: '280px' }}
              disabled={running}
            />
            <div style={{
              display: 'flex', justifyContent: 'space-between',
              marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--text-muted)',
            }}>
              <span>{text.length.toLocaleString()} characters</span>
              <span>{text.split(/\s+/).filter(Boolean).length.toLocaleString()} words</span>
            </div>
          </div>
        )}

        {/* Upload area */}
        {mode === 'upload' && (
          <div
            className="card"
            onDrop={handleDrop}
            onDragOver={e => e.preventDefault()}
            onClick={() => fileRef.current?.click()}
            style={{
              border: '2px dashed var(--border-primary)',
              textAlign: 'center',
              padding: '3rem 2rem',
              cursor: 'pointer',
              transition: 'border-color var(--transition-fast)',
              marginBottom: '1.5rem',
            }}
          >
            <Upload size={36} style={{ color: 'var(--text-muted)', marginBottom: '1rem' }} />
            {file ? (
              <div>
                <p style={{ fontWeight: 500 }}>{file.name}</p>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                  {(file.size / 1024).toFixed(1)} KB — Click or drop to replace
                </p>
              </div>
            ) : (
              <div>
                <p style={{ fontWeight: 500, color: 'var(--text-secondary)' }}>
                  Drop a file here or click to browse
                </p>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                  Supports PDF, DOCX, and TXT (max 10 MB)
                </p>
              </div>
            )}
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.doc,.txt"
              style={{ display: 'none' }}
              onChange={e => {
                const f = e.target.files?.[0];
                if (f) setFile(f);
              }}
            />
          </div>
        )}

        {/* Error */}
        {error && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.5rem',
            padding: '0.85rem 1rem', background: 'var(--danger-bg)',
            border: '1px solid rgba(239,68,68,0.2)', borderRadius: 'var(--radius-sm)',
            color: 'var(--danger)', fontSize: '0.875rem', marginBottom: '1.5rem',
          }}>
            <AlertCircle size={16} />
            {error}
          </div>
        )}

        {/* Submit */}
        <button
          className="btn btn-primary btn-lg"
          onClick={handleSubmit}
          disabled={running || (mode === 'text' ? !text.trim() : !file)}
        >
          {running ? (
            <>
              <Loader size={18} className="spinner" style={{
                animation: 'spin 0.8s linear infinite', border: 'none',
                width: 18, height: 18,
              }} />
              {stage}
            </>
          ) : (
            'Run Plagiarism Check'
          )}
        </button>
      </div>
    </div>
  );
}
