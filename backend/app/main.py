import json
import os
import queue
import re
import shutil
import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .jobs import registry
from .pipeline import capabilities, run_job

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "2048"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
ALLOWED_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

app = FastAPI(title="VideoSplat")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/capabilities")
def get_capabilities():
    return {**capabilities(), "max_upload_mb": MAX_UPLOAD_MB}


@app.post("/api/jobs")
def create_job(video: UploadFile = File(...)):
    suffix = Path(video.filename or "").suffix.lower()
    is_video_type = (video.content_type or "").startswith("video/")
    if suffix not in ALLOWED_SUFFIXES and not is_video_type:
        raise HTTPException(415, "Please upload a video file (mp4, mov, webm, …)")

    job = registry.create()
    video_path = job.dir / f"input{suffix if suffix in ALLOWED_SUFFIXES else '.mp4'}"

    written = 0
    with open(video_path, "wb") as out:
        while chunk := video.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                out.close()
                video_path.unlink(missing_ok=True)
                shutil.rmtree(job.dir, ignore_errors=True)
                raise HTTPException(413, f"Video is larger than {MAX_UPLOAD_MB} MB")
            out.write(chunk)
    if written == 0:
        raise HTTPException(400, "Uploaded file is empty")

    job.emit(status="queued", stage="upload", progress=0.0, message="Video received")
    threading.Thread(target=run_job, args=(job, video_path), daemon=True).start()
    return {"job_id": job.id, "mode": capabilities()["mode"]}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = registry.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return job.snapshot()


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str):
    job = registry.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")

    def stream():
        q = job.subscribe()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("status") in ("done", "error"):
                    return
        finally:
            job.unsubscribe(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/files/{name}")
def job_file(job_id: str, name: str):
    job = registry.get(job_id)
    if not job or not SAFE_NAME.fullmatch(name):
        raise HTTPException(404, "Not found")
    path = (job.dir / name).resolve()
    if job.dir.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="application/octet-stream", filename=name)


# In production (`npm run build`), serve the compiled frontend from this server.
_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
