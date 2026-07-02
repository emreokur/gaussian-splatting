"""In-memory job registry with SSE-friendly event fan-out.

Each job keeps its full event history so a subscriber that connects after the
pipeline has started (or an EventSource that reconnects) can replay from the
beginning; events carry a monotonically increasing `seq` so clients can dedupe.
"""

import queue
import threading
import time
import uuid
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "jobs"

TERMINAL_STATUSES = {"done", "error"}


class Job:
    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.dir = DATA_DIR / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.status = "queued"
        self.created_at = time.time()
        self._seq = 0
        self._events = []
        self._listeners = []
        self._lock = threading.Lock()

    def emit(self, **event):
        with self._lock:
            self._seq += 1
            event["seq"] = self._seq
            event.setdefault("status", self.status)
            self.status = event["status"]
            self._events.append(event)
            for q in self._listeners:
                q.put(event)

    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            for event in self._events:
                q.put(event)
            self._listeners.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def snapshot(self):
        with self._lock:
            last = self._events[-1] if self._events else None
        return {"job_id": self.id, "status": self.status, "last_event": last}

    @property
    def finished(self):
        return self.status in TERMINAL_STATUSES


class JobRegistry:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def create(self):
        job = Job()
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)


registry = JobRegistry()
