"""
Logging middleware for request tracking.
"""

import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

import structlog

logger = structlog.get_logger()


class LoggingMiddleware(BaseMiddleware):
    """Middleware that logs all incoming updates."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        start_time = time.time()

        # Extract useful info
        user_id = None
        message_type = type(event).__name__

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None

        logger.info(
            "Incoming update",
            type=message_type,
            user_id=user_id,
        )

        try:
            result = await handler(event, data)

            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "Update processed",
                type=message_type,
                user_id=user_id,
                elapsed_ms=elapsed_ms,
            )

            return result

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(
                "Update failed",
                type=message_type,
                user_id=user_id,
                elapsed_ms=elapsed_ms,
                error=str(e),
                exc_info=True,
            )
            raise
