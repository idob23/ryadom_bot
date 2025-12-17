"""
Database middleware - injects database session into handlers.
"""

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.db.session import async_session_factory


class DatabaseMiddleware(BaseMiddleware):
    """Middleware that provides database session to handlers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with async_session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
