from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


Progress = Callable[[int, str], None]
Task = Callable[[Progress, threading.Event], Any]
CancelHook = Callable[[], None]


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, threading.Event] = {}
        self._cancel_hooks: dict[str, CancelHook] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="deebee-job")

    def create(
        self, kind: str, task: Task, *, on_cancel: CancelHook | None = None
    ) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        event = threading.Event()
        job = {
            "id": job_id, "kind": kind, "status": "queued", "progress": 0,
            "message": "等待执行", "result": None, "error": "", "created_at": time.time(),
        }
        with self._lock:
            self._jobs[job_id] = job
            self._events[job_id] = event
            if on_cancel:
                self._cancel_hooks[job_id] = on_cancel
        self._executor.submit(self._run, job_id, task, event)
        return self.public(job_id)

    def _run(self, job_id: str, task: Task, event: threading.Event) -> None:
        self._update(job_id, status="running", progress=1, message="正在准备")
        try:
            result = task(lambda progress, message: self._update(job_id, progress=progress, message=message), event)
            if event.is_set():
                self._update(job_id, status="cancelled", message="已取消")
            else:
                self._update(job_id, status="completed", progress=100, message="执行完成", result=result)
        except Exception as exc:  # errors are serialized for the polling client
            if event.is_set():
                self._update(job_id, status="cancelled", message="已取消")
            else:
                self._update(job_id, status="failed", message="执行失败", error=str(exc))
        finally:
            with self._lock:
                self._cancel_hooks.pop(job_id, None)

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(values)

    def public(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return dict(job)

    def cancel(self, job_id: str) -> dict[str, Any]:
        hook: CancelHook | None = None
        with self._lock:
            event = self._events.get(job_id)
            if not event:
                raise KeyError(job_id)
            event.set()
            hook = self._cancel_hooks.get(job_id)
            if self._jobs[job_id]["status"] in {"queued", "running"}:
                self._jobs[job_id].update(status="cancelling", message="正在取消")
        if hook:
            try:
                hook()
            except Exception:
                # The task observes the cancellation event as a fallback. The
                # worker, not this request, owns the terminal job status.
                pass
        return self.public(job_id)


jobs = JobManager()
