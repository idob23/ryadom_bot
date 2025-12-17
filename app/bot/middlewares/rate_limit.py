"""
Rate limiting middleware using Redis.
"""

from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

import structlog

logger = structlog.get_logger()


class RateLimitMiddleware(BaseMiddleware):
    """
    Rate limiting middleware.

    Limits messages per user per minute to prevent spam/abuse.
    Uses Redis for distributed rate limiting.
    """

    def __init__(
        self,
        rate_limit: int = 10,  # messages per minute
        redis_client: Optional[Any] = None,
    ):
        self.rate_limit = rate_limit
        self.redis = redis_client
        # In-memory fallback if Redis not available
        self._memory_store: Dict[int, list] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Only rate limit messages
        if not isinstance(event, Message):
            return await handler(event, data)

        user_id = event.from_user.id

        # Check rate limit
        if await self._is_rate_limited(user_id):
            logger.warning(
                "User rate limited",
                user_id=user_id,
            )
            await event.answer(
                "Слишком много сообщений. Подожди немного."
            )
            return None

        # Record this request
        await self._record_request(user_id)

        return await handler(event, data)

    async def _is_rate_limited(self, user_id: int) -> bool:
        """Check if user is rate limited."""
        if self.redis:
            return await self._check_redis_rate_limit(user_id)
        return self._check_memory_rate_limit(user_id)

    async def _record_request(self, user_id: int) -> None:
        """Record a request from user."""
        if self.redis:
            await self._record_redis_request(user_id)
        else:
            self._record_memory_request(user_id)

    async def _check_redis_rate_limit(self, user_id: int) -> bool:
        """Check rate limit using Redis."""
        import time
        key = f"rate_limit:{user_id}"
        current_time = int(time.time())
        window_start = current_time - 60  # 1 minute window

        # Get count of requests in window
        count = await self.redis.zcount(key, window_start, current_time)
        return count >= self.rate_limit

    async def _record_redis_request(self, user_id: int) -> None:
        """Record request in Redis."""
        import time
        key = f"rate_limit:{user_id}"
        current_time = int(time.time())

        pipe = self.redis.pipeline()
        pipe.zadd(key, {str(current_time): current_time})
        pipe.zremrangebyscore(key, 0, current_time - 60)  # Remove old entries
        pipe.expire(key, 120)  # Expire after 2 minutes
        await pipe.execute()

    def _check_memory_rate_limit(self, user_id: int) -> bool:
        """Check rate limit using in-memory store (fallback)."""
        import time
        current_time = time.time()
        window_start = current_time - 60

        if user_id not in self._memory_store:
            return False

        # Clean old entries and count
        self._memory_store[user_id] = [
            t for t in self._memory_store[user_id] if t > window_start
        ]
        return len(self._memory_store[user_id]) >= self.rate_limit

    def _record_memory_request(self, user_id: int) -> None:
        """Record request in memory store (fallback)."""
        import time
        if user_id not in self._memory_store:
            self._memory_store[user_id] = []
        self._memory_store[user_id].append(time.time())
