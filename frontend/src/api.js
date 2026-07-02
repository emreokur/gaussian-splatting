export function uploadVideo(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/jobs');
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        let detail = `Upload failed (${xhr.status})`;
        try {
          detail = JSON.parse(xhr.responseText).detail || detail;
        } catch {
          /* keep default */
        }
        reject(new Error(detail));
      }
    };
    xhr.onerror = () => reject(new Error('Upload failed — is the backend running on port 8000?'));
    const form = new FormData();
    form.append('video', file);
    xhr.send(form);
  });
}

export async function fetchCapabilities() {
  const res = await fetch('/api/capabilities');
  if (!res.ok) throw new Error('backend unreachable');
  return res.json();
}

/**
 * Subscribe to a job's server-sent events. Events carry a monotonically
 * increasing `seq`; the backend replays history on (re)connect, so we dedupe.
 */
export function subscribeToJob(jobId, onEvent) {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  let lastSeq = 0;
  source.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    if (event.seq <= lastSeq) return;
    lastSeq = event.seq;
    onEvent(event);
    if (event.status === 'done' || event.status === 'error') source.close();
  };
  return () => source.close();
}
