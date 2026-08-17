import enum
from datetime import datetime

from pydantic import BaseModel, Field

from src.schemas.summary import TenderSummaryResponse


class JobStatus(str, enum.Enum):
    """Жизненный цикл фоновой задачи суммаризации."""

    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class JobResponse(BaseModel):
    """Фоновая задача и её результат (если уже готов)."""

    job_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    filename: str
    model: str | None = None
    provider: str | None = None
    max_chars: int | None = None
    max_pages: int | None = None
    progress: str | None = Field(default=None, description="Текущий этап обработки")
    result: TenderSummaryResponse | None = None
    error: str | None = None
