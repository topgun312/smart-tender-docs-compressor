import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, UploadFile
from loguru import logger

from src.config import UPLOADS_DIR, settings
from src.db.job_store import JobStore
from src.schemas.jobs import JobResponse, JobStatus
from src.schemas.summary import (
    TenderSummary,
    TenderSummaryResponse,
)
from src.services.unified_llm_client import UnifiedLLMClient
from src.utils.ocr_extractor import (
    OCRError,
    extract_text_with_ocr,
    looks_scanned,
    page_count,
)
from src.utils.pdf_extractor import (
    PDFExtractionError,
    extract_text_from_pdf,
)
from src.utils.prompts import (
    TENDER_SYSTEM_PROMPT,
    build_tender_messages,
)
from src.utils.utils import _extract_json


class SummarizationError(Exception):
    """Возникает, когда текст из PDF не удаётся извлечь или вызов LLM завершается ошибкой."""


class TenderSummarizerService:
    """Бизнес-логика извлечения выжимки из тендерной документации."""

    def __init__(
        self,
        llm_client: UnifiedLLMClient | None = None,
        store: JobStore | None = None,
        uploads_dir: Path = UPLOADS_DIR,
    ) -> None:
        """Инициализировать сервис LLM-клиентом."""
        self.llm_client = llm_client or UnifiedLLMClient(settings)
        self.store = store
        self.uploads_dir = uploads_dir

    async def process_job(self, job_id: str) -> None:
        """Обработать одну задачу в фоне и сохранить результат."""
        if self.store is None:
            raise RuntimeError("Хранилище задач не настроено для сервиса")

        file_path = self.uploads_dir / f"{job_id}.pdf"
        try:
            job = self.store.get(job_id)
            if job is None:
                return
            self.store.update(
                job_id,
                status=JobStatus.processing.value,
                progress="Извлечение текста из PDF",
            )
            data = file_path.read_bytes()
            result = await self.summarize_bytes(
                data=data,
                model=job.model,
                provider=job.provider,
                max_chars=job.max_chars or 15000,
                max_pages=job.max_pages,
            )
            self.store.update(
                job_id,
                status=JobStatus.completed.value,
                progress="Готово",
                result=result.model_dump_json(),
                error=None,
            )
            logger.info("Задача {} завершена", job_id)
        except Exception as e:
            logger.exception("Задача {} не выполнена", job_id)
            self.store.update(
                job_id,
                status=JobStatus.failed.value,
                progress=None,
                error=str(e),
            )
        finally:
            file_path.unlink(missing_ok=True)

    async def queue_pdf(
        self,
        file: UploadFile,
        model: str | None = None,
        provider: str | None = None,
        max_chars: int = 15000,
        max_pages: int | None = None,
    ) -> JobResponse:
        """Проверить загруженный PDF, сохранить его и создать фоновую задачу."""
        if self.store is None:
            raise RuntimeError("Хранилище задач не настроено для сервиса")

        filename = file.filename or ""
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Загрузите файл в формате PDF")

        data = await file.read()
        job_id = self.store.create(
            filename=filename,
            model=model,
            provider=provider,
            max_chars=max_chars,
            max_pages=max_pages,
        )
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        (self.uploads_dir / f"{job_id}.pdf").write_bytes(data)
        return self.store.get(job_id)

    @staticmethod
    def _normalize_summary(data: dict) -> TenderSummary:
        """Собрать TenderSummary из распарсенного JSON-словаря."""
        return TenderSummary(
            contract_amount=str(data.get("contract_amount") or "не указано"),
            contract_date=str(data.get("contract_date") or "не указано"),
            contract_start_date=str(data.get("contract_start_date") or "не указано"),
            contract_end_date=str(data.get("contract_end_date") or "не указано"),
            deadlines=str(data.get("deadlines") or "не указано"),
            contractor_requirements=list(data.get("contractor_requirements") or []),
            fines=list(data.get("fines") or []),
            security_amount=str(data.get("security_amount") or "не указано"),
            security_type=str(data.get("security_type") or "не указано"),
            security_conditions=list(data.get("security_conditions") or []),
        )

    async def summarize_pdf(
        self,
        file: UploadFile,
        model: str | None = None,
        provider: str | None = None,
        max_chars: int = 15000,
        max_pages: int | None = None,
    ) -> TenderSummaryResponse:
        """Сформировать структурированную выжимку из загруженного тендерного PDF."""
        data = await file.read()
        return await self.summarize_bytes(
            data=data,
            model=model,
            provider=provider,
            max_chars=max_chars,
            max_pages=max_pages,
        )

    @staticmethod
    def _extract_and_ocr(
        data: bytes, max_pages: int | None, max_chars: int
    ) -> tuple[str, bool]:
        """Извлечь текст из PDF, при необходимости используя OCR для сканов."""
        try:
            document_text = extract_text_from_pdf(
                data, max_pages=max_pages, max_chars=max_chars
            )
        except PDFExtractionError:
            document_text = ""

        pages = page_count(data)
        if max_pages:
            pages = min(pages, max_pages)

        ocr_used = False
        if settings.ocr_enabled and looks_scanned(
            document_text, pages, settings.ocr_min_avg_chars
        ):
            logger.info("PDF выглядит как скан, запускаем OCR (страниц: {})", pages)
            try:
                ocr_text = extract_text_with_ocr(
                    data, max_pages=max_pages, max_chars=max_chars
                )
            except OCRError as e:
                logger.warning("OCR недоступен: {}", e)
                if not document_text:
                    raise SummarizationError(str(e)) from e
            else:
                if ocr_text:
                    document_text = ocr_text
                    ocr_used = True
                    logger.info("OCR: распознано символов: {}", len(document_text))

        if not document_text:
            raise SummarizationError(
                "Не удалось извлечь текст из PDF (возможно, файл — скан без OCR)."
            )
        return document_text, ocr_used

    async def summarize_bytes(
        self,
        data: bytes,
        model: str | None = None,
        provider: str | None = None,
        max_chars: int = 15000,
        max_pages: int | None = None,
    ) -> TenderSummaryResponse:
        """Сформировать структурированную выжимку из сырых байтов PDF."""
        document_text, ocr_used = await asyncio.to_thread(
            self._extract_and_ocr, data, max_pages, max_chars
        )
        logger.info("Извлечено символов: {}", len(document_text))

        try:
            response = await self.llm_client.chat(
                system=TENDER_SYSTEM_PROMPT,
                messages=build_tender_messages(document_text),
                model=model,
                provider=provider,
            )
        except Exception as e:
            logger.error("Ошибка LLM: {}", e)
            raise SummarizationError(f"Ошибка при обращении к LLM: {e}") from e

        effective_provider = provider or self.llm_client._detect_provider(
            response.model
        )
        parsed = _extract_json(response.text)
        if parsed is None:
            logger.warning(
                "Модель не вернула JSON, отдаём сырой текст. Ответ: {!r}",
                response.text[:500],
            )
            return TenderSummaryResponse(
                summary=TenderSummary(
                    contract_amount="не указано",
                    contract_date="не указано",
                    contract_start_date="не указано",
                    contract_end_date="не указано",
                    deadlines="не указано",
                    contractor_requirements=[],
                    fines=[],
                    security_amount="не указано",
                    security_type="не указано",
                    security_conditions=[],
                ),
                raw_text=response.text,
                model=response.model,
                provider=effective_provider,
                ocr_used=ocr_used,
            )

        summary = self._normalize_summary(parsed)
        return TenderSummaryResponse(
            summary=summary,
            raw_text=None,
            model=response.model,
            provider=effective_provider,
            ocr_used=ocr_used,
        )


@lru_cache
def get_tender_summarizer_service() -> TenderSummarizerService:
    """Вернуть кэшированный (синглтон) экземпляр сервиса суммаризации."""
    return TenderSummarizerService()


TenderSummarizerServiceDep = Annotated[
    TenderSummarizerService, Depends(get_tender_summarizer_service)
]
