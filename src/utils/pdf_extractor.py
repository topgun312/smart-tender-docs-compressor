from io import BytesIO

from loguru import logger
from pypdf import PdfReader


class PDFExtractionError(Exception):
    """Возникает, когда PDF не удаётся прочитать или в нём нет извлекаемого текста."""


def extract_text_from_pdf(
    data: bytes,
    max_pages: int | None = None,
    max_chars: int | None = None,
) -> str:
    """Извлечь текст из PDF-файла."""
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as e:
        raise PDFExtractionError(f"Невозможно открыть PDF: {e}") from e

    pages = reader.pages[:max_pages] if max_pages else reader.pages
    chunks: list[str] = []
    total = 0

    for page in pages:
        try:
            text = page.extract_text() or ""
        except Exception as e:
            logger.warning("Не удалось извлечь текст со страницы: {}", e)
            continue

        if not text.strip():
            continue

        if max_chars:
            remaining = max_chars - total
            if remaining <= 0:
                break
            chunks.append(text[:remaining])
            total += min(len(text), remaining)
        else:
            chunks.append(text)
            total += len(text)

    result = "\n\n--- PAGE BREAK ---\n\n".join(chunks).strip()
    if not result:
        raise PDFExtractionError(
            "Не удалось извлечь текст из PDF (возможно, файл — скан без OCR)."
        )
    return result
