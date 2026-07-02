import { useEffect, useRef, useState } from 'react';
import { fetchCapabilities, subscribeToJob, uploadVideo } from './api.js';
import ProgressPanel from './components/ProgressPanel.jsx';
import SplatViewer from './components/SplatViewer.jsx';
import UploadZone from './components/UploadZone.jsx';

export default function App() {
  const [phase, setPhase] = useState('idle'); // idle | uploading | processing | done | error
  const [uploadPct, setUploadPct] = useState(0);
  const [events, setEvents] = useState([]);
  const [modelUrl, setModelUrl] = useState(null);
  const [finalUrl, setFinalUrl] = useState(null);
  const [error, setError] = useState(null);
  const [caps, setCaps] = useState(null);
  const [fileName, setFileName] = useState(null);
  const unsubscribeRef = useRef(null);

  useEffect(() => {
    fetchCapabilities()
      .then(setCaps)
      .catch(() => setCaps({ mode: 'offline' }));
    return () => unsubscribeRef.current?.();
  }, []);

  async function handleFile(file) {
    unsubscribeRef.current?.();
    setPhase('uploading');
    setUploadPct(0);
    setEvents([]);
    setModelUrl(null);
    setFinalUrl(null);
    setError(null);
    setFileName(file.name);

    let jobId;
    try {
      ({ job_id: jobId } = await uploadVideo(file, setUploadPct));
    } catch (err) {
      setError(err.message);
      setPhase('error');
      return;
    }

    setPhase('processing');
    unsubscribeRef.current = subscribeToJob(jobId, (event) => {
      setEvents((prev) => [...prev, event]);
      if (event.checkpoint) setModelUrl(event.checkpoint);
      if (event.status === 'done') {
        if (event.model) {
          setModelUrl(event.model);
          setFinalUrl(event.model);
        }
        setPhase('done');
      } else if (event.status === 'error') {
        setError(event.message || 'Processing failed');
        setPhase('error');
      }
    });
  }

  function reset() {
    unsubscribeRef.current?.();
    setPhase('idle');
    setEvents([]);
    setModelUrl(null);
    setFinalUrl(null);
    setError(null);
    setFileName(null);
  }

  const busy = phase === 'uploading' || phase === 'processing';

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          VideoSplat
          <span className="brand-sub">video → 3D gaussian splats</span>
        </div>
        <div className="topbar-actions">
          {caps && caps.mode !== 'offline' && (
            <span
              className={`mode-badge mode-${caps.mode}`}
              title={
                caps.mode === 'full'
                  ? 'COLMAP + trainer detected: full 3D reconstruction'
                  : 'Preview mode: install COLMAP and Brush (or set GS_TRAIN_CMD) for full 3D reconstruction'
              }
            >
              {caps.mode === 'full' ? 'full reconstruction' : 'preview mode'}
            </span>
          )}
          {finalUrl && (
            <a className="btn" href={finalUrl} download="model.ply">
              Download .ply
            </a>
          )}
          {phase !== 'idle' && (
            <button className="btn btn-primary" onClick={reset} disabled={busy && !error}>
              New video
            </button>
          )}
        </div>
      </header>

      <main className="stage-area">
        <SplatViewer modelUrl={modelUrl} />

        {phase === 'idle' && (
          <div className="overlay">
            <UploadZone onFile={handleFile} maxUploadMb={caps?.max_upload_mb} />
          </div>
        )}

        {phase === 'error' && (
          <div className="overlay">
            <div className="error-card">
              <h2>Something went wrong</h2>
              <p>{error}</p>
              <button className="btn btn-primary" onClick={reset}>
                Try another video
              </button>
            </div>
          </div>
        )}

        {(busy || phase === 'done') && (
          <ProgressPanel
            phase={phase}
            uploadPct={uploadPct}
            events={events}
            mode={caps?.mode}
          />
        )}

        {fileName && (
          <div className="file-chip" title={fileName}>
            {fileName}
          </div>
        )}

        {modelUrl && (
          <div className="controls-hint">drag to orbit · scroll to zoom · right-drag to pan</div>
        )}
      </main>
    </div>
  );
}
