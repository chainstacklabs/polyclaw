"""OpenRouter LLM client for hedge discovery.

Async client for calling LLMs via OpenRouter API.
Used for extracting logical implications between markets.
"""

import asyncio
import os

import httpx

# =============================================================================
# CONFIGURATION
# =============================================================================

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Default model; override with POLYCLAW_HEDGE_MODEL or --model.
# Model quality matters - it must follow the JSON format and reject spurious
# correlations. The previous default (nvidia/nemotron-nano-9b-v2:free) was
# removed from OpenRouter. qwen3-235b-a22b-2507 is the validation model the
# sister repo (polymarket-alpha-bot) runs; it is paid but cheap.
DEFAULT_MODEL = os.environ.get("POLYCLAW_HEDGE_MODEL", "").strip() or "qwen/qwen3-235b-a22b-2507"

# Request settings
LLM_TIMEOUT = 60.0
LLM_MAX_RETRIES = 3


# =============================================================================
# LLM CLIENT
# =============================================================================


class LLMClient:
    """
    Async client for OpenRouter API.

    Used for:
    - Extracting logical implications between markets
    - Analyzing market relationships for hedge discovery
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        timeout: float = LLM_TIMEOUT,
    ):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY not set. "
                "Get a free key at https://openrouter.ai/keys"
            )

        self.model = model
        self.timeout = timeout
        self.base_url = OPENROUTER_BASE_URL

        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> str:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens in response

        Returns:
            The assistant's response text
        """
        client = await self._get_client()

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        for attempt in range(LLM_MAX_RETRIES):
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                choice = response.json()["choices"][0]
                content = choice["message"].get("content")
                if content:
                    return content
                # Some models (reasoning ones in particular) can return no
                # text, e.g. when they spend the output budget on reasoning.
                finish = choice.get("finish_reason")
                if attempt < LLM_MAX_RETRIES - 1:
                    await asyncio.sleep(1)
                    continue
                raise RuntimeError(
                    f"{self.model} returned no text (finish_reason={finish}); "
                    "try another model with --model / POLYCLAW_HEDGE_MODEL"
                )

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # Rate limited, wait and retry
                    wait_time = 2**attempt
                    await asyncio.sleep(wait_time)
                    continue
                raise

            except httpx.RequestError:
                if attempt < LLM_MAX_RETRIES - 1:
                    await asyncio.sleep(1)
                    continue
                raise

        raise RuntimeError(f"Failed after {LLM_MAX_RETRIES} attempts")

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


# =============================================================================
# SINGLETON ACCESS
# =============================================================================

_llm_client: LLMClient | None = None


def get_llm_client(model: str = DEFAULT_MODEL) -> LLMClient:
    """
    Get LLM client singleton.

    Args:
        model: Model identifier from OpenRouter

    Returns:
        LLMClient instance
    """
    global _llm_client

    if _llm_client is None or _llm_client.model != model:
        _llm_client = LLMClient(model=model)

    return _llm_client


async def close_llm_client() -> None:
    """Close the LLM client connection."""
    global _llm_client
    if _llm_client:
        await _llm_client.close()
        _llm_client = None
