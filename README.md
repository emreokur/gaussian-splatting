# VideoSplat — video → 3D gaussian splats, live in the browser

A single-page web app: drop in a video, watch a gaussian-splat model form in
real time in an interactive WebGL viewer, then orbit around it and download the
`.ply`.

```
┌──────────────┐  upload   ┌─────────────────────────────────────────┐
│ React SPA    │ ────────▶ │ FastAPI backend                         │
│  · upload UI │           │  1. ffmpeg  → extract frames            │
│  · SSE feed  │ ◀──────── │  2. COLMAP  → camera poses   (full mode)│
│  · 3D viewer │  progress │  3. trainer → optimize splats(full mode)│
│    (three.js │  + .ply   │     — or —                              │
│    + gsplat) │  models   │  2. preview reconstructor (always works)│
└──────────────┘           └─────────────────────────────────────────┘
```

## Quick start

```bash
./dev.sh
# then open http://localhost:5173
```

Or run the two halves yourself:

```bash
# backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --port 8000

# frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Requirements: Python 3.10+, Node 18+, and `ffmpeg` on your PATH.

For production, `cd frontend && npm run build` — the backend then serves the
built app itself at http://localhost:8000.

## What's real time and what isn't (please read)

Rendering gaussian splats is real time; **training** them is not, anywhere.
True 3D gaussian splatting needs camera poses from structure-from-motion
(COLMAP) plus minutes of GPU optimization. The app has two backends and picks
one automatically (the badge in the top bar shows which):

- **Full mode** — real multi-view reconstruction. Activates when `colmap` is
  on the PATH and a trainer is available. The server extracts up to 64 frames,
  runs COLMAP (feature extraction → matching → mapping → undistortion), then
  trains gaussians, republishing the trainer's intermediate `.ply` exports to
  the viewer live. Expect a few minutes per video, not seconds.

  Setup on macOS (Apple Silicon — no CUDA needed; training runs on Metal via
  [Brush](https://github.com/ArthurBrussee/brush)):

  ```bash
  brew install colmap
  # drop the brush_app binary from Brush's GitHub releases into backend/tools/
  # (or put it on PATH / point BRUSH_BIN at it)
  ```

  To use a different trainer (e.g. the [INRIA reference
  trainer](https://github.com/graphdeco-inria/gaussian-splatting) on a CUDA
  machine), set `GS_TRAIN_CMD` — a shell template with `{data}`, `{images}`
  and `{output}` placeholders; it takes precedence over Brush:

  ```bash
  export GS_TRAIN_CMD='python /path/to/gaussian-splatting/train.py -s {data} -m {output}'
  ```

  Capture advice: orbit slowly around a static, well-textured subject
  (20–60 s, full circle if possible). COLMAP will fail on videos without
  camera translation or texture — the app then falls back to preview mode
  and says so in the log.

- **Preview mode** — fallback when COLMAP or a trainer is missing: a
  relief-style splat cloud built from the video's own pixels (color from a
  keyframe, depth from shading and per-pixel motion). Streams refinement
  checkpoints within seconds, but it is a stylized effect, **not** a true
  multi-view reconstruction.

## Configuration

| Env var          | Default | Description                                     |
| ---------------- | ------- | ----------------------------------------------- |
| `MAX_UPLOAD_MB`  | `2048`  | Maximum video upload size, in megabytes         |
| `GS_TOTAL_STEPS` | `6000`  | Training steps in full mode (quality vs. speed) |
| `BRUSH_BIN`      | unset   | Path to the Brush binary, if not auto-detected  |
| `GS_TRAIN_CMD`   | unset   | Custom trainer template (overrides Brush)       |

e.g. `MAX_UPLOAD_MB=2048 .venv/bin/python -m uvicorn app.main:app --port 8000`
raises the upload limit to 2 GB; the upload UI picks it up automatically.

## API

| Method | Path                          | Description                          |
| ------ | ----------------------------- | ------------------------------------ |
| GET    | `/api/capabilities`           | Which pipeline mode is active        |
| POST   | `/api/jobs` (multipart)       | Upload a video, returns `job_id`     |
| GET    | `/api/jobs/{id}/events`       | SSE stream of progress + checkpoints |
| GET    | `/api/jobs/{id}/files/{name}` | Checkpoint / final `model.ply`       |

The `.ply` files use the standard INRIA 3DGS layout, so they open in any splat
viewer (SuperSplat, antimatter15/splat, Polycam, …).

## Repo layout

- `frontend/` — Vite + React app; the viewer is
  [`@mkkellogg/gaussian-splats3d`](https://github.com/mkkellogg/GaussianSplats3D)
  on three.js.
- `backend/` — FastAPI app: `app/pipeline.py` (frame extraction + both
  reconstruction backends), `app/ply.py` (3DGS `.ply` writer), `app/jobs.py`
  (job registry + SSE fan-out). Uploads and outputs land in `backend/data/`.
