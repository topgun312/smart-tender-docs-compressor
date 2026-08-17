from pydantic import BaseModel, Field


class TenderSummary(BaseModel):
    """Ключевые условия контракта, извлечённые из тендерной документации."""

    contract_amount: str = Field(default="не указано", description="Сумма контракта")
    contract_date: str = Field(
        default="не указано", description="Дата заключения контракта"
    )
    contract_start_date: str = Field(
        default="не указано", description="Дата начала исполнения контракта"
    )
    contract_end_date: str = Field(
        default="не указано", description="Дата окончания исполнения контракта"
    )
    deadlines: str = Field(default="не указано", description="Сроки выполнения работ")
    contractor_requirements: list[str] = Field(
        default_factory=list, description="Требования к исполнителю"
    )
    fines: list[str] = Field(
        default_factory=list, description="Список штрафов и неустоек"
    )
    security_amount: str = Field(
        default="не указано", description="Размер обеспечения исполнения контракта"
    )
    security_type: str = Field(
        default="не указано",
        description="Способ обеспечения (банковская гарантия, денежные средства и т.п.)",
    )
    security_conditions: list[str] = Field(
        default_factory=list, description="Условия по обеспечению исполнения контракта"
    )


class TenderSummaryResponse(BaseModel):
    """API-обёртка вокруг выжимки по тендеру."""

    summary: TenderSummary
    raw_text: str | None = Field(
        default=None, description="Сырой ответ модели (если JSON не распарсился)"
    )
    model: str
    provider: str
    ocr_used: bool = Field(
        default=False, description="Был ли применён OCR (документ был сканом)"
    )
