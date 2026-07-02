const STAGES = [
  { key: 'upload', label: 'Upload video' },
  { key: 'frames', label: 'Extract frames' },
  { key: 'poses', label: 'Estimate camera poses', fullModeOnly: true },
  { key: 'reconstruct', label: 'Build gaussian splats' },
  { key: 'train', label: 'Optimize gaussians', fullModeOnly: true },
  { key: 'done', label: 'Model ready' },
];

export default function ProgressPanel({ phase, uploadPct, events, mode }) {
  const last = events[events.length - 1] || {};
  const stageOrder = STAGES.filter((s) => mode === 'full' || !s.fullModeOnly);
  const currentStage = phase === 'uploading' ? 'upload' : last.stage || 'upload';
  const currentIdx = Math.max(
    0,
    stageOrder.findIndex((s) => s.key === currentStage)
  );

  const progress =
    phase === 'uploading'
      ? uploadPct * 0.15
      : Math.max(0.15, ...events.map((e) => e.progress).filter((p) => p != null));

  const recentLogs = events
    .filter((e) => e.message)
    .slice(-4)
    .map((e) => e.message);
  const splats = [...events].reverse().find((e) => e.splats)?.splats;

  return (
    <aside className="progress-panel">
      <div className="progress-bar-track">
        <div
          className="progress-bar-fill"
          style={{ width: `${Math.round(progress * 100)}%` }}
        />
      </div>

      <ol className="stage-list">
        {stageOrder.map((stage, i) => {
          let state = 'pending';
          if (phase === 'done' || i < currentIdx) state = 'done';
          else if (i === currentIdx) state = phase === 'error' ? 'error' : 'active';
          return (
            <li key={stage.key} className={`stage stage-${state}`}>
              <span className="stage-dot" aria-hidden />
              {stage.label}
            </li>
          );
        })}
      </ol>

      {splats && (
        <div className="splat-count">{splats.toLocaleString()} splats</div>
      )}

      <div className="log-lines">
        {phase === 'uploading' && (
          <div>Uploading… {Math.round(uploadPct * 100)}%</div>
        )}
        {recentLogs.map((line, i) => (
          <div key={i} className={i === recentLogs.length - 1 ? 'log-current' : ''}>
            {line}
          </div>
        ))}
      </div>
    </aside>
  );
}
