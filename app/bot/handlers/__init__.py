from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.chat import router as chat_router
from app.bot.handlers.commands import router as commands_router
from app.bot.handlers.subscription import router as subscription_router

__all__ = ["admin_router", "chat_router", "commands_router", "subscription_router"]
