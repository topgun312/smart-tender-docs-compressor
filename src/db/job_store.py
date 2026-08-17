import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from src.config import DB_PATH
from src.schemas.jobs import JobResponse, JobStatus
from src.schemas.summary import TenderSummaryResponse


class JobStore:
    """Хранилище фоновых задач суммаризации на базе SQLite."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id     TEXT PRIMARY KEY,
                    status     TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    filename   TEXT NOT NULL,
                    model      TEXT,
                    provider   TEXT,
                    max_chars  INTEGER,
                    max_pages  INTEGER,
                    progress   TEXT,
                    result     TEXT,
                    error      TEXT
                )
                """)
            self._conn.commit()

    def create(
        self,
        filename: str,
        model: str | None,
        provider: str | None,
        max_chars: int,
        max_pages: int | None,
    ) -> str:
        """Создать задачу со статусом pending и вернуть её id."""
        job_id = str(uuid4())
        now = datetime.now().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (job_id, status, created_at, updated_at, filename, "
                "model, provider, max_chars, max_pages) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    JobStatus.pending.value,
                    now,
                    now,
                    filename,
                    model,
                    provider,
                    max_chars,
                    max_pages,
                ),
            )
            self._conn.commit()
        return job_id

    def get(self, job_id: str) -> JobResponse | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._row_to_job(row) if row else None

    def list(self, limit: int = 20) -> list[JobResponse]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def update(self, job_id: str, **fields) -> None:
        """Обновить произвольные поля задачи."""
        if not fields:
            return
        fields["updated_at"] = datetime.now().isoformat()
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = [*fields.values(), job_id]
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {cols} WHERE job_id = ?", values)
            self._conn.commit()

    def fail_stale(self) -> None:
        """Пометить задачи, «зависшие» в status 'processing' (например, после перезапуска), как failed."""
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, error = ? WHERE status = ?",
                (
                    JobStatus.failed.value,
                    datetime.now().isoformat(),
                    "Обработка прервана перезапуском сервера",
                    JobStatus.processing.value,
                ),
            )
            self._conn.commit()

    def cleanup(self, ttl_hours: int) -> int:
        """Удалить задачи старше ttl_hours; вернуть количество удалённых строк."""
        cutoff = (datetime.now() - timedelta(hours=ttl_hours)).isoformat()
        with self._lock:
            cur = self._conn.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _row_to_job(self, row) -> JobResponse:
        result = (
            TenderSummaryResponse.model_validate(json.loads(row[10]))
            if row[10]
            else None
        )
        return JobResponse(
            job_id=row[0],
            status=JobStatus(row[1]),
            created_at=row[2],
            updated_at=row[3],
            filename=row[4],
            model=row[5],
            provider=row[6],
            max_chars=row[7],
            max_pages=row[8],
            progress=row[9],
            result=result,
            error=row[11],
        )
