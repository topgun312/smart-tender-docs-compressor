import shutil
from io import BytesIO

import pymupdf
import pytesseract
from loguru import logger
from PIL import Image
from pypdf import PdfReader

from src.config import settings


class OCRError(Exception):
    """Возникает, когда OCR запрошен, но недоступен или завершился ошибкой."""


def is_tesseract_available() -> bool:
    """Проверить, установлен ли бинарник tesseract в системе."""
    return shutil.which("tesseract") is not None


def available_langs() -> list[str]:
    """Вернуть установленные языковые пакеты tesseract."""
    if not is_tesseract_available():
        return []
    try:
        langs = pytesseract.get_languages(config="")
        return [lang for lang in langs if lang != "osd"]
    except Exception as e:
        logger.warning("Не удалось получить список языков tesseract: {}", e)
        return []


def resolve_ocr_lang(requested: str) -> str:
    """Отфильтровать запрошенную строку языков по фактически установленным пакетам."""
    langs = available_langs()
    if not langs:
        return ""
    parts = [p for p in requested.split("+") if p in langs]
    return "+".join(parts) if parts else langs[0]


def page_count(data: bytes) -> int:
    """Вернуть количество страниц в PDF-документе."""
    try:
        return len(PdfReader(BytesIO(data)).pages)
    except Exception as e:
        logger.warning("Не удалось подсчитать страницы PDF: {}", e)
        return 0


def looks_scanned(text: str, pages: int, min_avg_chars: int = 100) -> bool:
    """Эвристика: похож ли извлечённый текст на скан без текстового слоя."""
    if pages <= 0:
        return True
    return len(text) / pages < min_avg_chars


def extract_text_with_ocr(
    data: bytes,
    max_pages: int | None = None,
    max_chars: int | None = None,
    lang: str | None = None,
) -> str:
    """Распознать текст PDF: рендер страниц в изображение и запуск tesseract."""
    if not is_tesseract_available():
        raise OCRError(
            "OCR недоступен: не установлен tesseract. "
            "Выполните: sudo apt install tesseract-ocr tesseract-ocr-rus"
        )

    lang = lang or settings.ocr_lang
    resolved = resolve_ocr_lang(lang)
    if not resolved:
        raise OCRError(
            "OCR недоступен: не найдено ни одного языкового пакета tesseract."
        )

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        pages = doc[:max_pages] if max_pages else doc
        chunks: list[str] = []
        total = 0

        for page in pages:
            pix = page.get_pixmap(dpi=settings.ocr_dpi)
            image = Image.open(BytesIO(pix.tobytes("png")))
            try:
                text = pytesseract.image_to_string(image, lang=resolved).strip()
            except Exception as e:
                logger.error("Ошибка OCR на странице: {}", e)
                text = ""

            if not text:
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

        return "\n\n--- PAGE BREAK ---\n\n".join(chunks).strip()
    finally:
        doc.close()
