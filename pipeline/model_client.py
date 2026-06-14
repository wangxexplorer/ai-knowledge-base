"""Unified OpenAI-compatible LLM client.

This module provides one small abstraction over DeepSeek, Qwen, and OpenAI
chat-completions APIs while keeping transport logic based on direct ``httpx``
requests instead of provider SDKs. A module-level ``CostTracker`` records
token usage and estimates cost in RMB for domestic model pricing.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any

LOGGER = logging.getLogger(__name__)

PROVIDER_ENV_VAR = "LLM_PROVIDER"
DEFAULT_PROVIDER = "deepseek"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_RETRY_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
CHAT_COMPLETIONS_PATH = "/chat/completions"
AUTHORIZATION_HEADER = "Authorization"
CONTENT_TYPE_HEADER = "Content-Type"
JSON_CONTENT_TYPE = "application/json"
BEARER_PREFIX = "Bearer"
APPROX_CHARS_PER_TOKEN = 4
TOKENS_PER_MILLION = 1_000_000


@dataclass(frozen=True)
class Usage:
    """Token usage returned by an LLM provider.

    Attributes:
        prompt_tokens: Number of input tokens consumed.
        completion_tokens: Number of output tokens produced.
        total_tokens: Total tokens reported by the provider.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class LLMResponse:
    """Normalized LLM response.

    Attributes:
        content: Assistant text content.
        usage: Token usage statistics.
        model: Model name used for the request.
        provider: Provider name used for the request.
    """

    content: str
    usage: Usage
    model: str
    provider: str


@dataclass(frozen=True)
class ProviderConfig:
    """Static configuration for a supported provider.

    Attributes:
        provider_name: Internal provider identifier.
        base_url: OpenAI-compatible API base URL.
        chat_path: Chat completions path relative to the base URL.
        api_key_env: Primary environment variable containing the API key.
        fallback_api_key_envs: Additional provider-compatible API key variables.
        default_model: Default chat model.
    """

    provider_name: str
    base_url: str
    chat_path: str
    api_key_env: str
    fallback_api_key_envs: tuple[str, ...]
    default_model: str


@dataclass(frozen=True)
class ModelPricing:
    """USD pricing per one million tokens.

    Attributes:
        input_per_million: Input-token price in USD per million tokens.
        output_per_million: Output-token price in USD per million tokens.
    """

    input_per_million: float
    output_per_million: float


class LLMClientError(RuntimeError):
    """Base exception for LLM client errors."""


class LLMConfigurationError(LLMClientError):
    """Raised when provider configuration is missing or invalid."""


class LLMResponseError(LLMClientError):
    """Raised when a provider response cannot be normalized."""


class LLMTransientError(LLMClientError):
    """Raised when a request may succeed after retrying."""


class LLMProvider(ABC):
    """Abstract interface for LLM chat providers."""

    @abstractmethod
    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send chat messages and return a normalized response.

        Args:
            messages: OpenAI-style chat messages.
            model: Optional model override.
            temperature: Sampling temperature.
            max_tokens: Optional completion token cap.

        Returns:
            Normalized LLM response.
        """


PROVIDER_CONFIGS: Mapping[str, ProviderConfig] = {
    "deepseek": ProviderConfig(
        provider_name="deepseek",
        base_url="https://api.deepseek.com",
        chat_path=CHAT_COMPLETIONS_PATH,
        api_key_env="DEEPSEEK_API_KEY",
        fallback_api_key_envs=(),
        default_model="deepseek-chat",
    ),
    "qwen": ProviderConfig(
        provider_name="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        chat_path=CHAT_COMPLETIONS_PATH,
        api_key_env="QWEN_API_KEY",
        fallback_api_key_envs=("DASHSCOPE_API_KEY",),
        default_model="qwen-plus",
    ),
    "openai": ProviderConfig(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        chat_path=CHAT_COMPLETIONS_PATH,
        api_key_env="OPENAI_API_KEY",
        fallback_api_key_envs=(),
        default_model="gpt-4o-mini",
    ),
}

MODEL_PRICING_USD: Mapping[str, ModelPricing] = {
    "deepseek-chat": ModelPricing(input_per_million=0.27, output_per_million=1.10),
    "deepseek-reasoner": ModelPricing(input_per_million=0.55, output_per_million=2.19),
    "qwen-plus": ModelPricing(input_per_million=0.40, output_per_million=1.20),
    "qwen-turbo": ModelPricing(input_per_million=0.05, output_per_million=0.20),
    "gpt-4o-mini": ModelPricing(input_per_million=0.15, output_per_million=0.60),
    "gpt-4o": ModelPricing(input_per_million=2.50, output_per_million=10.00),
}

# ──────────────────────────────────────────────────────────────────────
# Cost tracking (RMB pricing for domestic LLM providers)
# ──────────────────────────────────────────────────────────────────────

PROVIDER_PRICING_CNY: Mapping[str, Mapping[str, float]] = {
    "deepseek": {"input": 1.0, "output": 2.0},
    "qwen": {"input": 4.0, "output": 12.0},
    "openai": {"input": 150.0, "output": 600.0},
}
"""Provider pricing in RMB per million tokens.

Each entry maps ``input`` and ``output`` prices in CNY (元).  ``openai``
pricing defaults to gpt-4o-mini rates.
"""


@dataclass
class CostRecord:
    """Single LLM API call record for cost tracking.

    Attributes:
        provider: Provider identifier (deepseek, qwen, openai).
        model: Model name used for the request.
        prompt_tokens: Input tokens consumed.
        completion_tokens: Output tokens produced.
        timestamp: Monotonic timestamp of the call.
    """

    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    timestamp: float


class CostTracker:
    """Track LLM API token usage and estimate costs in RMB.

    Usage::

        tracker = enable_cost_tracking()
        # ... run pipeline — each LLM call is auto-recorded ...
        tracker.report()

    Attributes:
        _records: List of recorded API calls.
    """

    def __init__(self) -> None:
        """Initialize an empty cost tracker."""
        self._records: list[CostRecord] = []

    def record(
        self,
        usage: Usage,
        provider: str,
        model: str = "",
    ) -> None:
        """Record one API call's token usage.

        Args:
            usage: Token usage statistics from the LLM response.
            provider: Provider identifier (e.g. ``deepseek``, ``qwen``,
                ``openai``).
            model: Optional model name for display purposes.
        """
        self._records.append(
            CostRecord(
                provider=provider,
                model=model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                timestamp=time.monotonic(),
            )
        )

    def _provider_records(self, provider: str = "") -> list[CostRecord]:
        """Filter records by provider.

        Args:
            provider: Provider name filter, or empty for all.

        Returns:
            Matching records.
        """
        if not provider:
            return list(self._records)
        return [r for r in self._records if r.provider == provider]

    def estimated_cost(self, provider: str = "") -> float:
        """Calculate estimated cost in RMB.

        Args:
            provider: Provider name filter, or empty for all providers.

        Returns:
            Estimated total cost in RMB. Unknown providers contribute zero.
        """
        total = 0.0
        for record in self._provider_records(provider):
            pricing = PROVIDER_PRICING_CNY.get(record.provider, {})
            input_price = pricing.get("input", 0.0)
            output_price = pricing.get("output", 0.0)
            total += (
                record.prompt_tokens * input_price / TOKENS_PER_MILLION
                + record.completion_tokens * output_price / TOKENS_PER_MILLION
            )
        return total

    def report(self, provider: str = "") -> None:
        """Print a cost report to the logging output.

        Args:
            provider: Provider name to filter, or empty for all providers.
        """
        records = self._provider_records(provider)
        if not records:
            LOGGER.info("CostTracker: No records to report.")
            return

        total_prompt = sum(r.prompt_tokens for r in records)
        total_completion = sum(r.completion_tokens for r in records)
        total_cost = self.estimated_cost(provider)

        label = provider or "all providers"
        LOGGER.info("=" * 50)
        LOGGER.info("CostTracker Report (%s)", label)
        LOGGER.info("=" * 50)
        LOGGER.info("  Calls:           %d", len(records))
        LOGGER.info("  Prompt tokens:   %d", total_prompt)
        LOGGER.info("  Completion tok:  %d", total_completion)
        LOGGER.info("  Total tokens:    %d", total_prompt + total_completion)
        LOGGER.info("  Estimated cost:  ¥%.4f", total_cost)

        providers = {r.provider for r in records}
        if len(providers) > 1 and not provider:
            LOGGER.info("  --- Per Provider ---")
            for pv in sorted(providers):
                cost = self.estimated_cost(pv)
                pv_records = [r for r in records if r.provider == pv]
                pv_tokens = sum(
                    r.prompt_tokens + r.completion_tokens for r in pv_records
                )
                LOGGER.info(
                    "  %-12s %d calls, %d tokens, ¥%.4f",
                    pv,
                    len(pv_records),
                    pv_tokens,
                    cost,
                )
        LOGGER.info("=" * 50)


# Module-level tracker singleton.  Call :func:`enable_cost_tracking` to
# activate automatic recording in every ``LLMProvider.chat`` call.
cost_tracker: CostTracker | None = None


def enable_cost_tracking() -> CostTracker:
    """Create and activate the global cost tracker.

    All subsequent ``LLMProvider.chat`` calls will automatically record
    token usage to the returned tracker.

    Returns:
        The newly created :class:`CostTracker` instance.
    """
    global cost_tracker
    cost_tracker = CostTracker()
    return cost_tracker


class OpenAICompatibleProvider(LLMProvider):
    """Provider implementation for OpenAI-compatible chat APIs."""

    def __init__(
        self,
        provider_name: str,
        api_key: str,
        base_url: str,
        default_model: str,
        chat_path: str = CHAT_COMPLETIONS_PATH,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize an OpenAI-compatible provider.

        Args:
            provider_name: Internal provider name.
            api_key: Provider API key.
            base_url: API base URL without the chat completions path.
            default_model: Model used when callers do not pass one.
            chat_path: Chat completions path relative to the base URL.
            timeout_seconds: HTTP timeout in seconds.
        """
        self.provider_name = provider_name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.chat_path = chat_path if chat_path.startswith("/") else f"/{chat_path}"
        self.timeout_seconds = timeout_seconds

    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send chat messages to an OpenAI-compatible endpoint.

        Args:
            messages: OpenAI-style chat messages.
            model: Optional model override.
            temperature: Sampling temperature.
            max_tokens: Optional completion token cap.

        Returns:
            Normalized LLM response.
        """
        selected_model = model or self.default_model
        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": list(messages),
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        response_data = await self._post_json(payload)
        result = self.parse_response(response_data, model=selected_model)
        if cost_tracker is not None:
            cost_tracker.record(
                result.usage, self.provider_name, selected_model
            )
        return result

    def parse_response(self, data: Mapping[str, Any], model: str) -> LLMResponse:
        """Normalize OpenAI-compatible response JSON.

        Args:
            data: Response JSON object.
            model: Model name used for the request.

        Returns:
            Normalized LLM response.

        Raises:
            LLMResponseError: If required response fields are missing.
        """
        try:
            choices = data["choices"]
            first_choice = choices[0]
            message = first_choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("Missing choices[0].message.content") from exc

        if not isinstance(content, str):
            raise LLMResponseError("Response content must be a string")

        usage_data = data.get("usage", {})
        if not isinstance(usage_data, Mapping):
            usage_data = {}

        usage = Usage(
            prompt_tokens=_int_from_mapping(usage_data, "prompt_tokens"),
            completion_tokens=_int_from_mapping(usage_data, "completion_tokens"),
            total_tokens=_int_from_mapping(usage_data, "total_tokens"),
        )
        return LLMResponse(
            content=content,
            usage=usage,
            model=model,
            provider=self.provider_name,
        )

    async def _post_json(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """POST JSON with httpx and return response JSON.

        Args:
            payload: JSON request payload.

        Returns:
            Response JSON object.

        Raises:
            LLMConfigurationError: If httpx is not installed.
            LLMTransientError: If the network or server returns retryable errors.
            LLMClientError: If the server returns a non-retryable error.
            LLMResponseError: If the response body is not a JSON object.
        """
        try:
            httpx: Any = import_module("httpx")
        except ImportError as exc:
            raise LLMConfigurationError(
                "httpx is required for LLM HTTP calls. "
                "Install it before calling chat()."
            ) from exc

        url = f"{self.base_url}{self.chat_path}"
        headers = {
            AUTHORIZATION_HEADER: f"{BEARER_PREFIX} {self.api_key}",
            CONTENT_TYPE_HEADER: JSON_CONTENT_TYPE,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=dict(payload),
                )
                response.raise_for_status()
                response_data = response.json()
        except httpx.TimeoutException as exc:
            raise LLMTransientError("LLM request timed out") from exc
        except httpx.NetworkError as exc:
            raise LLMTransientError("LLM network error") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429 or 500 <= status_code < 600:
                raise LLMTransientError(
                    f"LLM provider returned retryable HTTP {status_code}"
                ) from exc
            raise LLMClientError(
                f"LLM provider returned non-retryable HTTP {status_code}"
            ) from exc
        except ValueError as exc:
            raise LLMResponseError("LLM response body is not valid JSON") from exc

        if not isinstance(response_data, Mapping):
            raise LLMResponseError("LLM response JSON must be an object")
        return response_data


def _int_from_mapping(data: Mapping[str, Any], key: str) -> int:
    """Read an integer value from a mapping with a default of zero.

    Args:
        data: Mapping containing provider usage fields.
        key: Field name to read.

    Returns:
        Integer field value, or zero when absent/non-integer.
    """
    value = data.get(key, 0)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def get_provider(provider_name: str | None = None) -> OpenAICompatibleProvider:
    """Create a provider from environment configuration.

    Args:
        provider_name: Optional provider override. When absent, ``LLM_PROVIDER``
            is used and defaults to DeepSeek.

    Returns:
        Configured OpenAI-compatible provider.

    Raises:
        LLMConfigurationError: If provider name or API key is invalid.
    """
    selected_provider = provider_name
    if selected_provider is None:
        selected_provider = os.getenv(PROVIDER_ENV_VAR)
    normalized_provider = (selected_provider or DEFAULT_PROVIDER).strip().lower()
    if not normalized_provider:
        normalized_provider = DEFAULT_PROVIDER
    config = PROVIDER_CONFIGS.get(normalized_provider)
    if config is None:
        supported = ", ".join(sorted(PROVIDER_CONFIGS))
        raise LLMConfigurationError(
            f"Unsupported LLM provider '{selected_provider}'. Supported: {supported}"
        )

    api_key = _get_first_env_value(
        (config.api_key_env, *config.fallback_api_key_envs)
    )
    if not api_key:
        env_names = ", ".join((config.api_key_env, *config.fallback_api_key_envs))
        raise LLMConfigurationError(
            f"Missing API key environment variable. Set one of: {env_names}"
        )

    return OpenAICompatibleProvider(
        provider_name=config.provider_name,
        api_key=api_key,
        base_url=config.base_url,
        default_model=config.default_model,
        chat_path=config.chat_path,
    )


def _get_first_env_value(names: Sequence[str]) -> str | None:
    """Return the first non-empty environment value from candidate names.

    Args:
        names: Environment variable names to inspect in order.

    Returns:
        First non-empty value, or ``None`` when all are unset.
    """
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


async def async_chat_with_retry(
    messages: Sequence[dict[str, str]],
    provider: LLMProvider | None = None,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int | None = None,
    attempts: int = MAX_RETRY_ATTEMPTS,
    sleep_func: Callable[[float], Any] = asyncio.sleep,
) -> LLMResponse:
    """Call chat asynchronously with retry and exponential backoff.

    Args:
        messages: OpenAI-style chat messages.
        provider: Optional provider instance. Defaults to environment provider.
        model: Optional model override.
        temperature: Sampling temperature.
        max_tokens: Optional completion token cap.
        attempts: Number of attempts before surfacing the final error.
        sleep_func: Sleep callable, injectable for tests.

    Returns:
        Normalized LLM response.

    Raises:
        LLMClientError: If all retry attempts fail or configuration is invalid.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    selected_provider = provider or get_provider()
    for attempt_index in range(attempts):
        try:
            return await selected_provider.chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMTransientError:
            is_last_attempt = attempt_index == attempts - 1
            if is_last_attempt:
                LOGGER.exception("LLM chat failed after %s attempts", attempts)
                raise
            delay = BACKOFF_BASE_SECONDS * (2**attempt_index)
            LOGGER.warning(
                "Transient LLM error on attempt %s/%s; retrying in %.1fs",
                attempt_index + 1,
                attempts,
                delay,
            )
            await _sleep(delay, sleep_func)

    raise LLMClientError("Retry loop ended unexpectedly")


def chat_with_retry(
    messages: Sequence[dict[str, str]],
    provider: LLMProvider | None = None,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int | None = None,
    attempts: int = MAX_RETRY_ATTEMPTS,
    sleep_func: Callable[[float], Any] = time.sleep,
) -> LLMResponse:
    """Call chat with retry and exponential backoff.

    Args:
        messages: OpenAI-style chat messages.
        provider: Optional provider instance. Defaults to environment provider.
        model: Optional model override.
        temperature: Sampling temperature.
        max_tokens: Optional completion token cap.
        attempts: Number of attempts before surfacing the final error.
        sleep_func: Sleep callable, injectable for tests.

    Returns:
        Normalized LLM response.

    Raises:
        LLMClientError: If called from an existing event loop or if all retry
            attempts fail.
    """
    _raise_if_running_event_loop("async_chat_with_retry")
    return asyncio.run(
        async_chat_with_retry(
            messages=messages,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            attempts=attempts,
            sleep_func=sleep_func,
        )
    )


async def _sleep(
    delay: float,
    sleep_func: Callable[[float], Any],
) -> None:
    """Sleep with either synchronous or asynchronous sleep callables.

    Args:
        delay: Seconds to sleep.
        sleep_func: Callable used to sleep.
    """
    result = sleep_func(delay)
    if inspect.isawaitable(result):
        await result


def _raise_if_running_event_loop(async_function_name: str) -> None:
    """Reject sync wrappers from inside an active event loop.

    Args:
        async_function_name: Async alternative to suggest in the error message.

    Raises:
        LLMClientError: If an event loop is already running in this thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise LLMClientError(
        f"Use {async_function_name}() when calling from an active event loop."
    )


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length.

    Args:
        text: Text to estimate.

    Returns:
        Approximate token count using four characters per token.
    """
    if not text:
        return 0
    return math.ceil(len(text) / APPROX_CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: Sequence[Mapping[str, str]]) -> int:
    """Estimate token count for chat messages.

    Args:
        messages: Chat messages containing role/content strings.

    Returns:
        Approximate total message token count.
    """
    return sum(
        estimate_tokens(message.get("role", ""))
        + estimate_tokens(message.get("content", ""))
        for message in messages
    )


def calculate_cost_usd(model: str, usage: Usage) -> float:
    """Calculate estimated USD cost for a response.

    Args:
        model: Model name used for pricing lookup.
        usage: Token usage statistics.

    Returns:
        Estimated USD cost. Unknown models return zero.
    """
    pricing = MODEL_PRICING_USD.get(model)
    if pricing is None:
        return 0.0

    input_cost = usage.prompt_tokens * pricing.input_per_million / TOKENS_PER_MILLION
    output_cost = (
        usage.completion_tokens * pricing.output_per_million / TOKENS_PER_MILLION
    )
    return input_cost + output_cost


async def async_quick_chat(
    prompt: str,
    provider: LLMProvider | None = None,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int | None = None,
    sleep_func: Callable[[float], Any] = asyncio.sleep,
) -> str:
    """Send one user prompt asynchronously and return assistant content.

    Args:
        prompt: User prompt text.
        provider: Optional provider instance. Defaults to environment provider.
        model: Optional model override.
        temperature: Sampling temperature.
        max_tokens: Optional completion token cap.
        sleep_func: Sleep callable, injectable for tests.

    Returns:
        Assistant response content.
    """
    response = await async_chat_with_retry(
        messages=[{"role": "user", "content": prompt}],
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        sleep_func=sleep_func,
    )
    return response.content


def quick_chat(
    prompt: str,
    provider: LLMProvider | None = None,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int | None = None,
    sleep_func: Callable[[float], Any] = time.sleep,
) -> str:
    """Send one user prompt and return assistant content.

    Args:
        prompt: User prompt text.
        provider: Optional provider instance. Defaults to environment provider.
        model: Optional model override.
        temperature: Sampling temperature.
        max_tokens: Optional completion token cap.
        sleep_func: Sleep callable, injectable for tests.

    Returns:
        Assistant response content.

    Raises:
        LLMClientError: If called from an existing event loop.
    """
    _raise_if_running_event_loop("async_quick_chat")
    return asyncio.run(
        async_quick_chat(
            prompt=prompt,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            sleep_func=sleep_func,
        )
    )


def main() -> int:
    """Run a simple manual smoke test.

    Returns:
        Process exit code. Zero means the request succeeded.
    """
    logging.basicConfig(level=logging.INFO)
    prompt = "用一句话介绍 AI Knowledge Base Assistant 的作用。"
    try:
        response = chat_with_retry([{"role": "user", "content": prompt}])
    except LLMClientError as exc:
        LOGGER.error("LLM smoke test failed: %s", exc)
        return 1

    cost = calculate_cost_usd(response.model, response.usage)
    LOGGER.info("Provider: %s", response.provider)
    LOGGER.info("Model: %s", response.model)
    LOGGER.info("Usage: %s", response.usage)
    LOGGER.info("Estimated cost USD: %.6f", cost)
    LOGGER.info("Content: %s", response.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
