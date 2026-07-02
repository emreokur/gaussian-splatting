import { useRef, useState } from 'react';

export default function UploadZone({ onFile, disabled, maxUploadMb }) {
  const inputRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);

  function handleFiles(files) {
    const file = files && files[0];
    if (!file || disabled) return;
    onFile(file);
  }

  return (
    <div
      className={`upload-zone${dragOver ? ' drag-over' : ''}`}
      onClick={() => !disabled && inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        handleFiles(e.dataTransfer.files);
      }}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && !disabled && inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept="video/*,.mp4,.mov,.m4v,.webm,.avi,.mkv"
        hidden
        onChange={(e) => {
          handleFiles(e.target.files);
          e.target.value = '';
        }}
      />
      <div className="upload-icon" aria-hidden>
        ⬆
      </div>
      <h2>Drop a video here</h2>
      <p>
        or click to choose a file — mp4, mov or webm
        {maxUploadMb ? `, up to ${maxUploadMb.toLocaleString()} MB` : ''}
      </p>
      <p className="upload-hint">
        For best results, orbit slowly around a static subject with plenty of texture.
      </p>
    </div>
  );
}
