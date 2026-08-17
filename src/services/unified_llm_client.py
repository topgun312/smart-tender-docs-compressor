import time
from dataclasses import dataclass
from typing import AsyncIterator

import anthropic
import openai
from loguru import logger

from src.config import LLMSettings, settings

TOKENS_PRICES: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
}


@dataclass
class LLMResponse:
    """Нормализованный ответ LLM."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    finish_reason: str | None = None


@dataclass
class UsageStats:
    """Накопленная статистика использования."""

    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    errors: int = 0

    def add(self, response: LLMResponse) -> None:
        """Накопить данные о токенах и стоимости из одного ответа LLM."""
        self.total_requests += 1
        self.total_input_tokens += response.input_tokens
        self.total_output_tokens += response.output_tokens
        prices = TOKENS_PRICES.get(response.model, {"input": 0, "output": 0})
        cost = (
            response.input_tokens * prices["input"] / 1000000
            + response.output_tokens * prices["output"] / 1000000
        )
        self.total_cost_usd += cost

    def report(self) -> str:
        """Вернуть человекочитаемую сводку использования."""
        return (
            f"Requests: {self.total_requests} | "
            f"Tokens: {self.total_input_tokens}↑ {self.total_output_tokens}↓ | "
            f"Cost: ${self.total_cost_usd:.4f}"
        )


class UnifiedLLMClient:
    """
    Единый клиент для работы с любым LLM-провайдером.
    Поддерживает: Anthropic, OpenAI, Ollama.
    """

    def __init__(self, settings: LLMSettings) -> None:
        """Инициализировать клиент с лениво создаваемыми клиентами провайдеров."""
        self.settings = settings
        self.usage = UsageStats()
        self._anthropic: anthropic.AsyncAnthropic | None = None
        self._openai: openai.AsyncOpenAI | None = None
        self._ollama: openai.AsyncOpenAI | None = None

    def _get_anthropic(self) -> anthropic.AsyncAnthropic:
        """Создать и закэшировать асинхронный клиент Anthropic."""
        if self._anthropic is None:
            self._anthropic = anthropic.AsyncAnthropic(
                api_key=self.settings.get_key("anthropic"),
                max_retries=3,
            )
        return self._anthropic

    def _get_openai(self) -> openai.AsyncOpenAI:
        """Создать и закэшировать асинхронный клиент OpenAI."""
        if self._openai is None:
            self._openai = openai.AsyncOpenAI(
                api_key=self.settings.get_key("openai"),
                max_retries=3,
            )
        return self._openai

    def _get_ollama(self) -> openai.AsyncOpenAI:
        """Создать и закэшировать клиент Ollama (интерфейс, совместимый с OpenAI)."""
        if self._ollama is None:
            self._ollama = openai.AsyncOpenAI(
                api_key="ollama",
                base_url=f"{self.settings.ollama_base_url}/v1",
                max_retries=1,
            )
        return self._ollama

    def _detect_provider(self, model: str) -> str:
        """Определить провайдера по имени модели."""
        if model.startswith("claude"):
            return "anthropic"
        elif (
            model.startswith("gpt") or model.startswith("o1") or model.startswith("o3")
        ):
            return "openai"
        elif (
            model.startswith("llama")
            or model.startswith("mistral")
            or model.startswith("qwen")
        ):
            return "ollama"
        return self.settings.default_provider

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        provider: str | None = None,
    ) -> LLMResponse:
        """Отправить запрос на завершение чата в соответствующий провайдер."""
        model = model or self.settings.default_model
        max_tokens = max_tokens or self.settings.max_tokens
        provider = provider or self._detect_provider(model)

        t0 = time.monotonic()
        try:
            if provider == "anthropic":
                response = await self._chat_anthropic(
                    messages, model, system, temperature, max_tokens
                )
            else:
                response = await self._chat_openai(
                    messages, model, system, temperature, max_tokens, provider
                )

            self.usage.add(response)
            elapsed = time.monotonic() - t0
            logger.debug(
                "LLM call: model={} tokens={}/{} elapsed={:.2f}s",
                response.model,
                response.input_tokens,
                response.output_tokens,
                elapsed,
            )
            return response
        except Exception as e:
            self.usage.errors += 1
            logger.error("Ошибка LLM (провайдер={} модель={}): {}", provider, model, e)
            raise

    async def _chat_anthropic(
        self,
        messages: list[dict],
        model: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Отправить запрос на завершение чата в Anthropic и нормализовать ответ."""
        r = await self._get_anthropic().messages.create(
            model=model,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=messages,
        )
        return LLMResponse(
            text=r.content[0].text,
            model=r.model,
            input_tokens=r.usage.input_tokens,
            output_tokens=r.usage.output_tokens,
            finish_reason=r.stop_reason,
        )

    async def _chat_openai(
        self,
        messages: list[dict],
        model: str,
        system: str,
        temperature: float,
        max_tokens: int,
        provider: str,
    ) -> LLMResponse:
        """Отправить запрос на завершение чата через интерфейс, совместимый с OpenAI."""
        client = {
            "openai": self._get_openai,
            "ollama": self._get_ollama,
        }[provider]()

        full_messages = (
            [{"role": "system", "content": system}] + messages if system else messages
        )
        r = await client.chat.completions.create(
            model=model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            text=r.choices[0].message.content,
            model=r.model,
            input_tokens=r.usage.prompt_tokens,
            output_tokens=r.usage.completion_tokens,
            finish_reason=r.choices[0].finish_reason,
        )

    async def stream(
        self,
        messages: list[dict],
        model: str | None = None,
        system: str = "",
        **kwargs,
    ) -> AsyncIterator[str]:
        """Стримить токены ответа чата от соответствующего провайдера."""
        model = model or self.settings.default_model
        provider = self._detect_provider(model)

        if provider == "anthropic":
            client = self._get_anthropic()
            async with client.messages.stream(
                model=model,
                system=system,
                messages=messages,
                max_tokens=kwargs.get("max_tokens", self.settings.max_tokens),
            ) as s:
                async for chunk in s.text_stream:
                    yield chunk
        else:
            oai_client = {
                "openai": self._get_openai,
                "ollama": self._get_ollama,
            }[provider]()
            msgs = (
                ([{"role": "system", "content": system}] + messages)
                if system
                else messages
            )
            async with await oai_client.chat.completions.create(
                model=model,
                messages=msgs,
                stream=True,
                max_tokens=kwargs.get("max_tokens", self.settings.max_tokens),
            ) as s:
                async for chunk in s:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta

    def print_usage(self) -> None:
        """Вывести накопленную статистику использования."""
        print(self.usage.report())


async def main() -> None:
    """Продемонстрировать работу клиента с несколькими провайдерами."""
    client = UnifiedLLMClient(settings)

    for model in ("claude-opus-4-6", "gpt-4o", "llama3.2"):
        try:
            r = await client.chat(
                system="Ты — ассистент.",
                messages=[{"role": "user", "content": "Привет!"}],
                model=model,
            )
            logger.info("модель={} ответ={!r}", r.model, r.text[:80])
        except Exception as e:
            logger.warning("модель={} не удалась: {}", model, e)

    client.print_usage()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
