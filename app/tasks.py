import time
import uuid
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("checkai.tasks")


@dataclass
class Task:
    id: str
    status: str = "pending"  # "pending", "processing", "completed", "failed"
    step: str = "upload"     # "upload", "ocr", "ai", "completed"
    progress: int = 10
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    file_path: Optional[str] = None
    image_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.id,
            "status": self.status,
            "step": self.step,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
        }


class TaskManager:
    """Менеджер фоновых задач распознавания чеков с очередью выполнения."""

    def __init__(self, max_workers: int = 1):
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ocr-worker")

    def create_task(self, file_path: str, image_url: str) -> Task:
        self.cleanup_old_tasks()
        task_id = uuid.uuid4().hex
        task = Task(id=task_id, file_path=file_path, image_url=image_url)
        with self._lock:
            self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def update_task(self, task_id: str, **kwargs):
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                for k, v in kwargs.items():
                    setattr(task, k, v)
                task.updated_at = time.time()

    def submit(self, task_id: str, run_fn):
        self._executor.submit(self._run_wrapper, task_id, run_fn)

    def _run_wrapper(self, task_id: str, run_fn):
        self.update_task(task_id, status="processing", step="ocr", progress=35)
        try:
            result = run_fn(task_id)
            self.update_task(
                task_id,
                status="completed",
                step="completed",
                progress=100,
                result=result,
            )
        except Exception as e:
            logger.exception("Ошибка фонового распознавания чека в задаче %s", task_id)
            self.update_task(
                task_id,
                status="failed",
                progress=100,
                error=getattr(e, "detail", str(e)),
            )

    def cleanup_old_tasks(self, max_age_seconds: int = 3600):
        """Удаляет завершённые задачи старше одного часа из памяти."""
        now = time.time()
        with self._lock:
            to_delete = [
                tid for tid, task in self._tasks.items()
                if now - task.updated_at > max_age_seconds
            ]
            for tid in to_delete:
                del self._tasks[tid]


task_manager = TaskManager(max_workers=1)
