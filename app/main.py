"""
Main entry point for Ryadom bot.
"""

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import structlog

from app.config import settings
from app.bot.handlers import chat_router, commands_router, subscription_router
from app.bot.middlewares import DatabaseMiddleware, LoggingMiddleware, RateLimitMiddleware
from app.core.claude import close_claude_client
from app.db.session import close_db, init_db
from app.utils.logging import setup_logging, setup_sentry


logger = structlog.get_logger()


async def on_startup(bot: Bot) -> None:
    """Startup tasks."""
    logger.info("Starting bot...")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Get bot info
    bot_info = await bot.get_me()
    logger.info(
        "Bot started",
        username=bot_info.username,
        id=bot_info.id,
    )


async def on_shutdown(bot: Bot) -> None:
    """Shutdown tasks."""
    logger.info("Shutting down bot...")

    # Close connections
    await close_claude_client()
    await close_db()

    logger.info("Bot stopped")


def create_dispatcher() -> Dispatcher:
    """Create and configure dispatcher."""
    dp = Dispatcher()

    # Register middlewares (order matters!)
    dp.message.middleware(LoggingMiddleware())
    dp.message.middleware(RateLimitMiddleware(rate_limit=10))
    dp.message.middleware(DatabaseMiddleware())

    dp.callback_query.middleware(LoggingMiddleware())
    dp.callback_query.middleware(DatabaseMiddleware())

    # Register routers (order matters - commands before general chat!)
    dp.include_router(commands_router)
    dp.include_router(subscription_router)
    dp.include_router(chat_router)

    # Register lifecycle handlers
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    return dp


def create_bot() -> Bot:
    """Create bot instance."""
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.MARKDOWN,
        ),
    )


async def main() -> None:
    """Main function."""
    # Setup logging
    setup_logging()
    setup_sentry()

    logger.info(
        "Configuration loaded",
        environment=settings.environment,
        debug=settings.debug,
    )

    # Create bot and dispatcher
    bot = create_bot()
    dp = create_dispatcher()

    # Start polling
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
