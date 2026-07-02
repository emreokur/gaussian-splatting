"""Video -> gaussian splat pipeline.

Two backends, selected automatically at runtime:

- "full":    real 3D reconstruction. Requires `colmap` on PATH for camera pose
             estimation, plus a trainer: either Brush (a Metal/Vulkan/CUDA
             gaussian-splatting trainer, auto-detected in backend/tools/, on
             PATH, or via BRUSH_BIN) or a custom command in GS_TRAIN_CMD.
             Takes minutes per video, streams training checkpoints live.

- "preview": built-in fallback that always works. Builds a relief-style splat
             cloud from the video's own pixels (color from the middle frame,
             depth from shading plus per-pixel motion across the clip) and
             emits progressively refined checkpoints. It is a preview effect,
             not a true multi-view reconstruction.
"""

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .ply import write_gaussian_ply

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"

PREVIEW_MAX_FRAMES = 28
PREVIEW_FRAME_WIDTH = 512
FULL_MAX_FRAMES = 64
FULL_FRAME_WIDTH = 960

GRID_WIDTH = 224          # splats per row in preview mode
WORLD_WIDTH = 3.2         # world-space width of the preview relief
PREVIEW_PASSES = 5


class PipelineError(Exception):
    pass


def find_brush():
    env = os.environ.get("BRUSH_BIN")
    if env and Path(env).is_file():
        return env
    bundled = TOOLS_DIR / "brush_app"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return shutil.which("brush_app") or shutil.which("brush")


def capabilities():
    colmap = shutil.which("colmap")
    train_cmd = os.environ.get("GS_TRAIN_CMD")
    brush = find_brush()
    full = bool(colmap and (train_cmd or brush))
    return {
        "mode": "full" if full else "preview",
        "ffmpeg": bool(FFMPEG),
        "colmap": bool(colmap),
        "trainer": bool(train_cmd or brush),
    }


# ---------------------------------------------------------------- frames ----

def _probe_duration(video_path):
    if not FFPROBE:
        return None
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.SubprocessError, ValueError):
        return None


def extract_frames(video_path, frames_dir, max_frames, width):
    if not FFMPEG:
        raise PipelineError("ffmpeg is required to decode video but was not found on PATH")
    frames_dir.mkdir(parents=True, exist_ok=True)

    duration = _probe_duration(video_path)
    fps = max_frames / duration if duration and duration > 0.5 else 4.0
    fps = min(fps, 30.0)

    result = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(video_path),
         "-vf", f"fps={fps:.6f},scale={width}:-2",
         "-frames:v", str(max_frames),
         str(frames_dir / "frame_%04d.png")],
        capture_output=True, text=True, timeout=600,
    )
    frames = sorted(frames_dir.glob("frame_*.png"))
    if result.returncode != 0 or not frames:
        detail = (result.stderr or "").strip().splitlines()
        raise PipelineError(
            "Could not decode the video" + (f": {detail[-1]}" if detail else "")
        )
    return frames


# ------------------------------------------------------------- preview ------

def _box_blur(a, radius):
    if radius <= 0:
        return a
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (radius, radius)
        p = np.pad(a, pad, mode="edge")
        c = np.cumsum(p, axis=axis)
        width = 2 * radius + 1
        a = (np.take(c, range(width - 1, p.shape[axis]), axis=axis)
             - np.concatenate([np.take(c, [0], axis=axis) * 0,
                               np.take(c, range(0, p.shape[axis] - width), axis=axis)],
                              axis=axis)) / width
    return a


def _load_grid(path, grid_w, grid_h):
    img = Image.open(path).convert("RGB").resize((grid_w, grid_h), Image.LANCZOS)
    return np.asarray(img, dtype=np.float32) / 255.0


def preview_reconstruction(frames, job, files_url):
    """Generate the preview splat cloud, emitting refinement checkpoints."""
    mid = Image.open(frames[len(frames) // 2])
    aspect = mid.height / mid.width
    grid_w = GRID_WIDTH
    grid_h = max(8, int(round(grid_w * aspect)))

    rgb = _load_grid(frames[len(frames) // 2], grid_w, grid_h)

    # Per-pixel motion across the clip: regions that change get pulled forward.
    step = max(1, len(frames) // 8)
    lums = [ _load_grid(f, grid_w, grid_h) @ np.array([0.299, 0.587, 0.114], np.float32)
             for f in frames[::step][:8] ]
    motion = np.stack(lums).std(axis=0)
    if motion.max() > 1e-6:
        motion = motion / motion.max()

    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    depth = 0.20 * _box_blur(lum - lum.mean(), 3) + 0.30 * _box_blur(motion, 4)

    world_h = WORLD_WIDTH * aspect
    us = (np.arange(grid_w, dtype=np.float32) + 0.5) / grid_w
    vs = (np.arange(grid_h, dtype=np.float32) + 0.5) / grid_h
    uu, vv = np.meshgrid(us, vs)

    # gsplat/COLMAP convention: +y is down, and the orbit camera starts on the
    # -z side looking toward +z — so image-down maps to +y and "toward the
    # viewer" is -z.
    positions = np.stack([
        (uu - 0.5) * WORLD_WIDTH,
        (vv - 0.5) * world_h,
        -depth,
    ], axis=-1).reshape(-1, 3)
    colors = rgb.reshape(-1, 3)

    n = positions.shape[0]
    spacing = WORLD_WIDTH / grid_w
    scales = np.full((n, 3), spacing * 0.62, dtype=np.float32)
    scales[:, 2] *= 0.5
    opacities = np.full(n, 0.92, dtype=np.float32)
    rotations = np.zeros((n, 4), dtype=np.float32)
    rotations[:, 0] = 1.0  # identity quaternion (w, x, y, z)

    rng = np.random.default_rng(7)
    order = rng.permutation(n)

    for k in range(1, PREVIEW_PASSES + 1):
        frac = k / PREVIEW_PASSES
        count = max(1, int(n * (0.12 + 0.88 * frac ** 1.4)))
        idx = order[:count]
        jitter = (1.0 - frac) * spacing * 5.0
        noise = rng.normal(0.0, jitter or 1e-9, (count, 3)).astype(np.float32)
        noise[:, 2] *= 0.5
        coarse = 1.0 + (1.0 - frac) * 1.2

        name = f"checkpoint_{k}.ply"
        write_gaussian_ply(
            job.dir / name,
            positions[idx] + noise,
            colors[idx],
            scales[idx] * coarse,
            opacities[idx],
            rotations[idx],
        )
        job.emit(
            status="processing", stage="reconstruct",
            progress=round(0.2 + 0.75 * frac, 3),
            message=f"Refinement pass {k}/{PREVIEW_PASSES} — {count:,} splats",
            checkpoint=f"{files_url}/{name}", splats=count,
        )
        if k < PREVIEW_PASSES:
            time.sleep(0.4)

    write_gaussian_ply(job.dir / "model.ply", positions, colors, scales,
                       opacities, rotations)
    return n


# ---------------------------------------------------------------- full ------

def _count_ply_vertices(path):
    try:
        with open(path, "rb") as f:
            header = b""
            while not header.endswith(b"end_header\n") and len(header) < 65536:
                chunk = f.read(1)
                if not chunk:
                    return None
                header += chunk
        m = re.search(rb"element vertex (\d+)", header)
        return int(m.group(1)) if m else None
    except OSError:
        return None


def _run_logged(cmd, job, stage, progress=None):
    """Run a subprocess, streaming its output as throttled SSE messages."""
    job.emit(status="processing", stage=stage, progress=progress,
             message="$ " + " ".join(Path(str(c)).name if os.sep in str(c) else str(c)
                                     for c in cmd))
    proc = subprocess.Popen(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    last, last_emit = "", 0.0
    for line in proc.stdout:
        line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        if line:
            last = line
            now = time.monotonic()
            if now - last_emit > 0.8:
                last_emit = now
                job.emit(status="processing", stage=stage, message=line[:300])
    proc.wait()
    if proc.returncode != 0:
        raise PipelineError(f"{Path(str(cmd[0])).name} failed during {stage}: {last[:300]}")


class _CheckpointWatcher:
    """Republish the trainer's exported .ply files as viewer checkpoints.

    Only publishes a file once its size is stable across two polls, so we
    never serve a checkpoint the trainer is still writing.
    """

    def __init__(self, job, output_dir, files_url, total_steps):
        self.job = job
        self.output_dir = Path(output_dir)
        self.files_url = files_url
        self.total_steps = total_steps
        self.stop = threading.Event()
        self.sizes = {}
        self.published = set()
        self.count = 0
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self.stop.is_set():
            self._scan()
            self.stop.wait(3.0)
        self._scan()  # final sweep after the trainer exits

    def _scan(self):
        for ply in sorted(self.output_dir.rglob("*.ply")):
            try:
                size = ply.stat().st_size
            except OSError:
                continue
            if ply in self.published or size == 0:
                continue
            if self.sizes.get(ply) != size:
                self.sizes[ply] = size  # still growing — check again next poll
                continue
            self.published.add(ply)
            self.count += 1
            name = f"train_{self.count}.ply"
            try:
                shutil.copyfile(ply, self.job.dir / name)
            except OSError:
                continue
            iter_match = re.search(r"(\d+)", ply.stem)
            step = int(iter_match.group(1)) if iter_match else None
            progress = None
            if step and self.total_steps:
                progress = round(min(0.99, 0.5 + 0.5 * step / self.total_steps), 3)
            splats = _count_ply_vertices(self.job.dir / name)
            self.job.emit(
                status="processing", stage="train", progress=progress,
                message=f"Training checkpoint at step {step:,}" if step
                        else f"Training checkpoint: {ply.name}",
                checkpoint=f"{self.files_url}/{name}",
                **({"splats": splats} if splats else {}),
            )


def full_reconstruction(frames_dir, job, files_url):
    """COLMAP pose estimation + gaussian-splatting training (Brush by default)."""
    colmap = shutil.which("colmap")
    work = job.dir / "colmap"
    sparse_raw = work / "sparse_raw"
    sparse_raw.mkdir(parents=True, exist_ok=True)
    db = work / "database.db"

    job.emit(status="processing", stage="poses", progress=0.18,
             message="Estimating camera poses with COLMAP (this takes a few minutes)…")
    _run_logged([colmap, "feature_extractor",
                 "--database_path", db, "--image_path", frames_dir,
                 "--ImageReader.single_camera", "1",
                 "--ImageReader.camera_model", "SIMPLE_RADIAL",
                 "--FeatureExtraction.use_gpu", "0"],
                job, "poses", progress=0.2)
    _run_logged([colmap, "exhaustive_matcher", "--database_path", db,
                 "--FeatureMatching.use_gpu", "0"],
                job, "poses", progress=0.3)
    _run_logged([colmap, "mapper", "--database_path", db,
                 "--image_path", frames_dir, "--output_path", sparse_raw],
                job, "poses", progress=0.38)

    models = [m for m in sparse_raw.iterdir() if m.is_dir() and (m / "images.bin").exists()]
    if not models:
        raise PipelineError(
            "COLMAP could not recover camera poses from this video. It needs "
            "camera movement around a static, textured scene — try slowly "
            "orbiting an object"
        )
    best = max(models, key=lambda m: (m / "images.bin").stat().st_size)

    dataset = job.dir / "dataset"
    _run_logged([colmap, "image_undistorter", "--image_path", frames_dir,
                 "--input_path", best, "--output_path", dataset,
                 "--output_type", "COLMAP"],
                job, "poses", progress=0.46)

    # Trainers expect the INRIA layout: dataset/images + dataset/sparse/0
    sparse = dataset / "sparse"
    zero = sparse / "0"
    if not zero.exists():
        zero.mkdir()
        for f in [p for p in sparse.iterdir() if p.is_file()]:
            shutil.move(str(f), zero / f.name)

    total_steps = int(os.environ.get("GS_TOTAL_STEPS", "6000"))
    output_dir = job.dir / "training"
    output_dir.mkdir(exist_ok=True)

    train_cmd = os.environ.get("GS_TRAIN_CMD")
    if train_cmd:
        cmd = ["bash", "-lc", train_cmd.format(
            data=str(dataset), images=str(dataset / "images"),
            output=str(output_dir))]
    else:
        cmd = [find_brush(), str(dataset),
               "--total-steps", str(total_steps),
               "--export-every", str(max(500, total_steps // 5)),
               "--export-path", str(output_dir),
               "--max-resolution", "1280"]

    job.emit(status="processing", stage="train", progress=0.5,
             message=f"Optimizing gaussians on the GPU ({total_steps:,} steps)…")

    watcher = _CheckpointWatcher(job, output_dir, files_url, total_steps)
    watcher.thread.start()
    try:
        _run_logged(cmd, job, "train")
    finally:
        watcher.stop.set()
        watcher.thread.join(timeout=10.0)

    def export_step(p):
        m = re.search(r"(\d+)", p.stem)
        return int(m.group(1)) if m else -1

    plys = sorted(output_dir.rglob("*.ply"), key=export_step)
    if not plys:
        raise PipelineError("Trainer finished but produced no .ply model")
    shutil.copyfile(plys[-1], job.dir / "model.ply")
    return _count_ply_vertices(job.dir / "model.ply")


# --------------------------------------------------------------- runner -----

def run_job(job, video_path):
    files_url = f"/api/jobs/{job.id}/files"
    try:
        caps = capabilities()
        full = caps["mode"] == "full"

        job.emit(status="processing", stage="frames", progress=0.03,
                 message="Extracting frames from video…")
        frames = extract_frames(
            video_path, job.dir / "frames",
            max_frames=FULL_MAX_FRAMES if full else PREVIEW_MAX_FRAMES,
            width=FULL_FRAME_WIDTH if full else PREVIEW_FRAME_WIDTH,
        )
        job.emit(status="processing", stage="frames", progress=0.15,
                 message=f"Extracted {len(frames)} frames")

        if full:
            try:
                splats = full_reconstruction(job.dir / "frames", job, files_url)
            except PipelineError as e:
                job.emit(status="processing", stage="reconstruct",
                         message=f"Full pipeline failed ({e}); falling back to preview mode")
                splats = preview_reconstruction(frames, job, files_url)
        else:
            job.emit(status="processing", stage="reconstruct", progress=0.2,
                     message="Building preview splat model (install COLMAP + Brush "
                             "for true reconstruction)")
            splats = preview_reconstruction(frames, job, files_url)

        event = {
            "status": "done", "stage": "done", "progress": 1.0,
            "message": "Model ready", "model": f"{files_url}/model.ply",
        }
        if splats:
            event["splats"] = splats
        job.emit(**event)
    except PipelineError as e:
        job.emit(status="error", stage="error", message=str(e))
    except Exception as e:  # noqa: BLE001 — surface anything to the client
        job.emit(status="error", stage="error", message=f"Unexpected error: {e!r}")
