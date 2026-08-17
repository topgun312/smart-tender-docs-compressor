from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile

from src.config import settings
from src.db.job_store import JobStore
from src.schemas import JobResponse
from src.services.service import TenderSummarizerService

store = JobStore()
service = TenderSummarizerService(store=store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Пометить задачи, прерванные перезапуском, как failed и закрыть БД при завершении."""
    store.fail_stale()
    try:
        yield
    finally:
        store.close()


app = FastAPI(
    title="Умный суммаризатор тендерной документации",
    description="Краткий обзор тендерной документации по государственным закупкам: "
    "ключевые положения, сроки, требования и штрафные санкции.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    """Вернуть статус сервиса и настроенные LLM-провайдеры."""
    return {
        "status": "ok",
        "providers": settings.validate_keys(),
        "default_provider": settings.default_provider,
        "default_model": settings.default_model,
    }


@app.post("/summarize", status_code=202, response_model=JobResponse)
async def summarize_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF-файл конкурсной документации"),
    model: str | None = Query(
        default=None, description="Модель LLM (по умолчанию из .env)"
    ),
    provider: str | None = Query(
        default=None, description="Провайдер: ollama | openai | anthropic"
    ),
    max_chars: int = Query(
        default=15000,
        ge=1000,
        le=200000,
        description="Лимит символов текста, отправляемых в LLM",
    ),
    max_pages: int | None = Query(
        default=None, ge=1, le=500, description="Лимит страниц PDF (с начала)"
    ),
) -> JobResponse:
    """Поставить загруженный тендерный PDF в очередь на фоновую суммаризацию."""
    job = await service.queue_pdf(
        file=file,
        model=model,
        provider=provider,
        max_chars=max_chars,
        max_pages=max_pages,
    )
    background_tasks.add_task(service.process_job, job.job_id)
    return job


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    """Вернуть текущий статус и, если готово, результат задачи."""
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return job


@app.get("/jobs", response_model=list[JobResponse])
async def list_jobs(limit: int = Query(default=20, ge=1, le=100)) -> list[JobResponse]:
    """Вернуть последние задачи (сначала самые новые)."""
    return store.list(limit)
