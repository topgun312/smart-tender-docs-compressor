from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = _BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "jobs.db"


class LLMSettings(BaseSettings):
    """Настройки приложения для LLM-провайдеров, загружаемые из окружения/.env."""

    model_config = SettingsConfigDict(
        env_file=_BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    default_provider: str = "anthropic"
    default_model: str = "claude-opus-4-6"
    max_tokens: int = 2048
    request_timeout: int = 60
    ollama_base_url: str = "http://localhost:11434"

    ocr_enabled: bool = True
    ocr_lang: str = "rus+eng"
    ocr_dpi: int = 200
    ocr_min_avg_chars: int = 100

    job_ttl_hours: int = 24

    def get_key(self, provider: str) -> str:
        """Вернуть API-ключ для указанного провайдера."""
        key_map = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
        }

        secret = key_map.get(provider)
        if secret is None:
            raise ValueError(f"API-ключ для '{provider}' не настроен")
        return secret.get_secret_value()

    def validate_keys(self) -> list[str]:
        """Вернуть список провайдеров с доступными учётными данными."""
        available = []
        if self.openai_api_key:
            available.append("openai")
        if self.anthropic_api_key:
            available.append("anthropic")
        available.append("ollama")
        return available


settings = LLMSettings()

available = settings.validate_keys()
print(f"Available providers: {available}")
