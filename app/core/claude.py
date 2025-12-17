"""
Claude API client with retry logic and error handling.
"""

import json
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import structlog

from app.config import settings
from app.core.prompts import (
    CONVERSATION_SUMMARY_PROMPT,
    MAIN_SYSTEM_PROMPT,
    MEMORY_EXTRACTION_PROMPT,
    MOOD_DETECTION_PROMPT,
    build_additional_context,
    build_user_context,
)

logger = structlog.get_logger()


@dataclass
class ClaudeResponse:
    """Response from Claude API."""
    content: str
    tokens_input: int
    tokens_output: int
    response_time_ms: int
    model: str


class ClaudeAPIError(Exception):
    """Custom exception for Claude API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, retryable: bool = False):
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(self.message)


class ClaudeClient:
    """Async Claude API client with retry logic."""

    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        model_fast: Optional[str] = None,
        max_tokens: int = 500,
        max_retries: int = 3,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or settings.claude_api_key
        self.model = model or settings.claude_model  # Sonnet for main responses
        self.model_fast = model_fast or settings.claude_model_fast  # Haiku for utilities
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.timeout = timeout

        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "x-api-key": self.api_key,
                    "content-type": "application/json",
                    "anthropic-version": self.API_VERSION,
                },
            )
        return self._client

    async def close(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _make_request(
        self,
        messages: list[dict],
        system: str,
        max_tokens: Optional[int] = None,
        use_fast_model: bool = False,
    ) -> ClaudeResponse:
        """Make API request with retry logic.

        Args:
            use_fast_model: If True, use Haiku instead of Sonnet (cheaper, faster)
        """
        client = await self._get_client()
        max_tokens = max_tokens or self.max_tokens
        model = self.model_fast if use_fast_model else self.model

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }

        last_error = None
        for attempt in range(self.max_retries):
            start_time = time.time()

            try:
                response = await client.post(self.API_URL, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    elapsed_ms = int((time.time() - start_time) * 1000)

                    return ClaudeResponse(
                        content=data["content"][0]["text"],
                        tokens_input=data["usage"]["input_tokens"],
                        tokens_output=data["usage"]["output_tokens"],
                        response_time_ms=elapsed_ms,
                        model=data["model"],
                    )

                # Handle specific error codes
                if response.status_code == 429:
                    # Rate limited - wait and retry
                    retry_after = int(response.headers.get("retry-after", 5))
                    logger.warning(
                        "Rate limited by Claude API",
                        retry_after=retry_after,
                        attempt=attempt + 1,
                    )
                    await self._sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    # Server error - retry
                    logger.warning(
                        "Claude API server error",
                        status_code=response.status_code,
                        attempt=attempt + 1,
                    )
                    await self._sleep(2 ** attempt)  # Exponential backoff
                    continue

                # Client error - don't retry
                error_data = response.json()
                error_message = error_data.get("error", {}).get("message", "Unknown error")
                raise ClaudeAPIError(
                    message=error_message,
                    status_code=response.status_code,
                    retryable=False,
                )

            except httpx.TimeoutException:
                logger.warning(
                    "Claude API timeout",
                    timeout=self.timeout,
                    attempt=attempt + 1,
                )
                last_error = ClaudeAPIError("Request timeout", retryable=True)
                await self._sleep(2 ** attempt)
                continue

            except httpx.RequestError as e:
                logger.warning(
                    "Claude API request error",
                    error=str(e),
                    attempt=attempt + 1,
                )
                last_error = ClaudeAPIError(f"Request error: {e}", retryable=True)
                await self._sleep(2 ** attempt)
                continue

        # All retries exhausted
        raise last_error or ClaudeAPIError("Max retries exceeded", retryable=False)

    async def _sleep(self, seconds: float):
        """Async sleep helper."""
        import asyncio
        await asyncio.sleep(seconds)

    async def get_response(
        self,
        user_data: dict,
        messages: list[tuple[str, str]],
        memories: list[dict],
        mood_history: list[dict],
        conversation_summaries: list[str],
        time_of_day: str = "day",
        days_since_last_chat: int = 0,
    ) -> ClaudeResponse:
        """
        Get chat response from Claude.

        Args:
            user_data: User profile data
            messages: List of (role, content) tuples
            memories: List of memory facts
            mood_history: Recent mood entries
            conversation_summaries: Previous conversation summaries
            time_of_day: morning/afternoon/evening/night
            days_since_last_chat: Days since last conversation
        """
        # Build context
        user_context = build_user_context(user_data, memories, mood_history)
        additional_context = build_additional_context(
            time_of_day, days_since_last_chat, conversation_summaries
        )

        # Build system prompt
        system = MAIN_SYSTEM_PROMPT.format(
            user_context=user_context,
            additional_context=additional_context,
        )

        # Convert messages to Claude format
        claude_messages = [
            {"role": role, "content": content}
            for role, content in messages
        ]

        return await self._make_request(claude_messages, system)

    async def extract_memories(
        self,
        conversation: list[tuple[str, str]],
        known_facts: list[str],
    ) -> list[dict]:
        """
        Extract memory facts from conversation.
        Uses Haiku for cost efficiency.

        Returns list of {"category": str, "fact": str, "importance": int}
        """
        conversation_text = "\n".join(
            f"{'Пользователь' if role == 'user' else 'Рядом'}: {content}"
            for role, content in conversation
        )

        prompt = MEMORY_EXTRACTION_PROMPT.format(
            conversation=conversation_text,
            known_facts="\n".join(f"- {f}" for f in known_facts) if known_facts else "Нет",
        )

        try:
            response = await self._make_request(
                messages=[{"role": "user", "content": prompt}],
                system="Ты — система извлечения информации. Отвечай только JSON.",
                max_tokens=1000,
                use_fast_model=True,  # Use Haiku
            )

            data = json.loads(response.content)
            return data.get("facts", [])

        except (json.JSONDecodeError, ClaudeAPIError) as e:
            logger.error("Failed to extract memories", error=str(e))
            return []

    async def detect_mood(
        self,
        message: str,
        context: list[tuple[str, str]],
    ) -> Optional[dict]:
        """
        Detect user's mood from message.
        Uses Haiku for cost efficiency.

        Returns {"mood_score": int, "energy_level": int, "anxiety_level": int,
                 "detected_emotions": list, "requires_attention": bool, "reason": str}
        """
        context_text = "\n".join(
            f"{'Пользователь' if role == 'user' else 'Рядом'}: {content}"
            for role, content in context[-5:]  # Last 5 messages
        )

        prompt = MOOD_DETECTION_PROMPT.format(
            message=message,
            context=context_text,
        )

        try:
            response = await self._make_request(
                messages=[{"role": "user", "content": prompt}],
                system="Ты — система анализа эмоций. Отвечай только JSON.",
                max_tokens=300,
                use_fast_model=True,  # Use Haiku
            )

            return json.loads(response.content)

        except (json.JSONDecodeError, ClaudeAPIError) as e:
            logger.error("Failed to detect mood", error=str(e))
            return None

    async def summarize_conversation(
        self,
        conversation: list[tuple[str, str]],
    ) -> Optional[str]:
        """
        Create summary of conversation for long-term storage.
        Uses Haiku for cost efficiency.
        """
        conversation_text = "\n".join(
            f"{'Пользователь' if role == 'user' else 'Рядом'}: {content}"
            for role, content in conversation
        )

        prompt = CONVERSATION_SUMMARY_PROMPT.format(conversation=conversation_text)

        try:
            response = await self._make_request(
                messages=[{"role": "user", "content": prompt}],
                system="Ты — система создания резюме разговоров. Отвечай кратко.",
                max_tokens=300,
                use_fast_model=True,  # Use Haiku
            )

            return response.content

        except ClaudeAPIError as e:
            logger.error("Failed to summarize conversation", error=str(e))
            return None


# Singleton client instance
_client: Optional[ClaudeClient] = None


def get_claude_client() -> ClaudeClient:
    """Get or create Claude client singleton."""
    global _client
    if _client is None:
        _client = ClaudeClient()
    return _client


async def close_claude_client():
    """Close Claude client."""
    global _client
    if _client:
        await _client.close()
        _client = None
