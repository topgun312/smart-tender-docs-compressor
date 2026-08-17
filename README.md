# Smart Tender Docs Compressor

FastAPI-сервис для автоматической выжимки ключевых условий из PDF-документации
госзакупок (zakupki.gov.ru). Принимает PDF, извлекает текст (с OCR для сканов)
и с помощью LLM возвращает структурированный JSON: сумму контракта, сроки,
требования к исполнителю, штрафы и условия обеспечения.

> Логика решения и алгоритм обработки описаны в [SOLUTION.md](SOLUTION.md).

Обработка выполняется в фоне: эндпоинт принимает файл, сохраняет его, сразу
возвращает `job_id`, а обработка (извлечение текста + LLM) запускается в фоне
в отдельной задаче; результат затем читается через `GET /jobs/{job_id}`.

## Стек

- **FastAPI** — веб-фреймворк (асинхронные эндпоинты, Pydantic-схемы)
- **pydantic / pydantic-settings** — валидация и конфигурация из `.env`
- **pypdf** — извлечение текста из PDF
- **PyMuPDF (pymupdf)** — рендер страниц для OCR
- **pytesseract + tesseract-ocr** — OCR для сканов
- **openai / anthropic** — SDK для провайдеров LLM
- **Ollama** — бесплатная локальная LLM (OpenAI-совместимый интерфейс)
- **SQLite** — хранение задач (встроенный `sqlite3`)
- **loguru** — логирование
- **uv** — управление зависимостями и окружением

## Возможности

- Единый LLM-клиент для нескольких провайдеров: **Ollama**, **OpenAI**, **Anthropic**.
  Провайдер определяется автоматически по имени модели либо задаётся явно.
- Автоматический OCR: если PDF — скан без текстового слоя, страницы рендерятся
  в изображения и распознаются через tesseract.
- Лимит страниц и символов текста, отправляемого в LLM (защита от переполнения контекста).
- Асинхронная обработка в фоне: `POST /summarize` запускает задачу и возвращает
  `job_id`, результат сохраняется в SQLite и читается через `GET /jobs/{job_id}`.
- Умный парсинг ответа модели: JSON достаётся даже из Markdown-обёртки и лишнего текста.
- Автоматический подсчёт стоимости и токенов для платных провайдеров.
- Чистая архитектура: `main` → `service` → `pdf_extractor` / `ocr_extractor` / `prompts` / `unified_llm_client`.

## Структура проекта

```
ai-projects/
├── pyproject.toml              # Зависимости проекта (uv)
├── uv.lock
├── src/
│   ├── main.py                 # FastAPI-приложение и эндпоинты
│   ├── config.py               # Настройки (pydantic-settings) и пути
│   ├── .env.example            # Шаблон переменных окружения
│   ├── db/
│   │   └── job_store.py        # SQLite-хранилище задач
│   ├── schemas/
│   │   ├── jobs.py             # JobResponse, JobStatus
│   │   └── summary.py          # TenderSummary, TenderSummaryResponse
│   ├── services/
│   │   ├── service.py          # Бизнес-логика (TenderSummarizerService)
│   │   └── unified_llm_client.py   # Единый клиент для Anthropic / OpenAI / Ollama
│   └── utils/
│       ├── pdf_extractor.py    # Извлечение текста из PDF
│       ├── ocr_extractor.py    # OCR через tesseract
│       ├── prompts.py          # Промпты для суммаризации тендеров
│       └── utils.py            # Вспомогательные функции (парсинг JSON)
└── data/                       # БД задач и загруженные PDF (создаётся автоматически)
```

## Установка

### 1. Системные зависимости

Для работы нужны Python 3.12+ и менеджер пакетов `uv`:

```bash
# uv (если ещё не установлен)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Для OCR сканов требуется tesseract с русским и английским языковыми пакетами:

```bash
# Ubuntu / Debian
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng

# macOS (Homebrew)
brew install tesseract tesseract-lang
```

Проверка установки:

```bash
tesseract --version
tesseract --list-langs   # должны быть rus и eng
```

### 2. Python-зависимости

Из корня репозитория (`ai-projects/`):

```bash
uv sync
```

### 3. Настройка `.env`

Скопируйте шаблон и отредактируйте:

```bash
cp .env.example .env
```

```bash
# OpenAI / Anthropic (опционально, если не используете Ollama)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Для Ollama (локально)
OLLAMA_BASE_URL=http://localhost:11434

# Провайдер и модель по умолчанию
DEFAULT_PROVIDER=ollama
DEFAULT_MODEL=llama3.2

# Лимиты
MAX_TOKENS=2048
REQUEST_TIMEOUT=60

# OCR для сканов (нужен установленный tesseract)
OCR_ENABLED=true
OCR_LANG=rus+eng
OCR_DPI=200
OCR_MIN_AVG_CHARS=100

# Хранение задач
JOB_TTL_HOURS=24
```

## Запуск

### 1. Поднимите Ollama (если используете локальную модель)

```bash
ollama pull llama3.2
ollama serve
```

### 2. Запустите сервис

Из корня репозитория:

```bash
uv run uvicorn src.main:app --reload
```

Swagger-документация: http://127.0.0.1:8000/docs

## Использование

### Загрузка PDF

```bash
curl -F "file=@document.pdf" http://127.0.0.1:8000/summarize
```

Параметры запроса:

| Параметр    | Описание                                            | По умолчанию |
|-------------|-----------------------------------------------------|--------------|
| `file`      | PDF-файл конкурсной документации (multipart)        | обязательно  |
| `model`     | Модель LLM (например `llama3.2`, `gpt-4o`)          | из `.env`    |
| `provider`  | Провайдер: `ollama` / `openai` / `anthropic`        | авто-детект  |
| `max_chars` | Лимит символов текста, отправляемых в LLM           | `15000`      |
| `max_pages` | Лимит страниц PDF (с начала)                        | без лимита   |

Ответ (статус `202`) — сразу возвращается созданная задача:

```json
{
  "job_id": "3f2c1a9e-...",
  "status": "pending",
  "created_at": "2026-08-16T12:00:00",
  "updated_at": "2026-08-16T12:00:00",
  "filename": "document.pdf",
  "model": null,
  "provider": null,
  "max_chars": 15000,
  "max_pages": null,
  "progress": null,
  "result": null,
  "error": null
}
```

### Проверка статуса задачи

```bash
curl http://127.0.0.1:8000/jobs/3f2c1a9e-...
```

Статусы: `pending` → `processing` → `completed` / `failed`. Когда `status == "completed"`,
в поле `result` появится структурированная выжимка:

```json
{
  "summary": {
    "contract_amount": "15 780 000,00 рублей",
    "deadlines": "с даты заключения контракта до 20 декабря 2026 года",
    "contractor_requirements": [
      "наличие действующей лицензии ФСБ",
      "опыт выполнения аналогичных работ за последние 3 года"
    ],
    "fines": [
      "пеня 1/300 ставки рефинансирования за каждый день просрочки",
      "штраф за неисполнение обязательств 10% от цены контракта"
    ]
  },
  "raw_text": null,
  "model": "llama3.2",
  "provider": "ollama"
}
```

### Список последних задач

```bash
curl "http://127.0.0.1:8000/jobs?limit=20"
```

### Проверка сервиса

```bash
curl http://127.0.0.1:8000/health
```

## Правила использования

- Принимаются **только файлы PDF** — иначе вернётся ошибка `400`.
- Файл сохраняется в `data/uploads/<job_id>.pdf`
  и удаляется после обработки.
- Ограничения запроса: `max_chars` от 1000 до 200000, `max_pages` от 1 до 500.
- Задачи старше `JOB_TTL_HOURS` (по умолчанию 24 ч) удаляются автоматически.
- Скан без текстового слоя обрабатывается OCR; если tesseract не установлен
  или ни один языковой пакет не подходит, задача завершится ошибкой.

## Переключение провайдера

Модель `llama3.2` автоматически направляется в Ollama, `gpt-4o` — в OpenAI,
`claude-*` — в Anthropic. Можно указать провайдера явно:

```bash
# Через Ollama
curl -F "file=@document.pdf" "http://127.0.0.1:8000/summarize?provider=ollama&model=llama3.2"

# Через OpenAI
curl -F "file=@document.pdf" "http://127.0.0.1:8000/summarize?provider=openai&model=gpt-4o-mini"
```

> Настройка Ollama описана в [OLLAMA.md](OLLAMA.md).