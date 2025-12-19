"""
Background scheduler for periodic tasks.

Handles:
- Proactive check-ins (users who haven't been around)
- Crisis follow-ups
- Conversation cleanup/summarization
"""

import asyncio
from datetime import datetime, time
from typing import Optional

import structlog
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.db.session import async_session_factory
from app.services.proactive import ProactiveService, should_followup_after_crisis
from app.db.repository import UserRepository, MoodRepository


logger = structlog.get_logger()


class BotScheduler:
    """
    Manages periodic background tasks.

    All times are in Moscow timezone (Europe/Moscow) since
    this is a Russian-language bot.
    """

    TIMEZONE = "Europe/Moscow"

    def __init__(self, bot: Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone=self.TIMEZONE)
        self._running = False

    def start(self):
        """Start the scheduler."""
        if self._running:
            return

        # Proactive check-ins - run at 11:00 and 19:00 Moscow time
        # Morning: catch people who might need support during the day
        # Evening: catch people who might feel lonely at night
        self.scheduler.add_job(
            self._run_proactive_checkins,
            CronTrigger(hour=11, minute=0, timezone=self.TIMEZONE),
            id="proactive_checkins_morning",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._run_proactive_checkins,
            CronTrigger(hour=19, minute=0, timezone=self.TIMEZONE),
            id="proactive_checkins_evening",
            replace_existing=True,
        )

        # Crisis follow-ups - check every 2 hours
        # More frequent because these are urgent
        self.scheduler.add_job(
            self._run_crisis_followups,
            CronTrigger(hour="*/2", minute=30, timezone=self.TIMEZONE),
            id="crisis_followups",
            replace_existing=True,
        )

        self.scheduler.start()
        self._running = True

        logger.info(
            "Scheduler started",
            jobs=[job.id for job in self.scheduler.get_jobs()],
        )

    def stop(self):
        """Stop the scheduler."""
        if not self._running:
            return

        self.scheduler.shutdown(wait=False)
        self._running = False
        logger.info("Scheduler stopped")

    async def _run_proactive_checkins(self):
        """Run proactive check-ins batch."""
        logger.info("Running proactive check-ins...")

        try:
            async with async_session_factory() as session:
                service = ProactiveService(session, self.bot)
                sent = await service.run_checkins(
                    min_days=3,  # Inactive for 3+ days
                    max_users=20,  # Max 20 per batch to not overwhelm
                )
                await session.commit()

                logger.info("Proactive check-ins completed", sent=sent)

        except Exception as e:
            logger.error("Failed to run proactive check-ins", error=str(e))

    async def _run_crisis_followups(self):
        """Follow up with users who had crisis moments."""
        logger.info("Running crisis follow-ups...")

        try:
            async with async_session_factory() as session:
                user_repo = UserRepository(session)

                # Get recently active users (might have had crisis)
                # This is a simplified approach - in production you'd want
                # a more efficient query
                from sqlalchemy import select
                from app.db.models import User
                from datetime import timedelta

                cutoff = datetime.utcnow() - timedelta(days=1)
                result = await session.execute(
                    select(User).where(
                        User.last_active_at >= cutoff,
                        User.is_active == True,
                    )
                )
                users = result.scalars().all()

                followups_sent = 0
                for user in users:
                    if await should_followup_after_crisis(session, user.id):
                        # Send gentle follow-up
                        message = await self._generate_crisis_followup(user)
                        if message:
                            try:
                                await self.bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=message,
                                )
                                followups_sent += 1
                                logger.info(
                                    "Sent crisis follow-up",
                                    user_id=user.id,
                                )
                            except Exception as e:
                                logger.warning(
                                    "Failed to send crisis follow-up",
                                    user_id=user.id,
                                    error=str(e),
                                )

                logger.info("Crisis follow-ups completed", sent=followups_sent)

        except Exception as e:
            logger.error("Failed to run crisis follow-ups", error=str(e))

    async def _generate_crisis_followup(self, user) -> Optional[str]:
        """Generate a gentle follow-up message after crisis."""
        from app.core.claude import get_claude_client

        claude = get_claude_client()

        prompt = f"""Создай короткое (1-2 предложения) мягкое сообщение для пользователя {user.name}.
Несколько часов назад он/она поделился тяжёлыми переживаниями.
Не упоминай конкретику, просто дай знать что ты рядом.
Не будь навязчивым. Не требуй ответа.
Пример: "Привет, {user.name}. Просто хотел сказать, что я здесь, если нужно." """

        try:
            response = await claude._make_request(
                messages=[{"role": "user", "content": prompt}],
                system="Ты — Рядом, эмоциональный компаньон. Пиши тепло и кратко.",
                max_tokens=100,
                use_fast_model=True,
            )
            return response.content.strip().strip('"')
        except Exception as e:
            logger.error("Failed to generate crisis follow-up", error=str(e))
            return f"Привет, {user.name}. Я рядом, если нужно поговорить. 💙"


# Global scheduler instance
_scheduler: Optional[BotScheduler] = None


def get_scheduler() -> Optional[BotScheduler]:
    """Get scheduler instance."""
    return _scheduler


def init_scheduler(bot: Bot) -> BotScheduler:
    """Initialize and start the scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BotScheduler(bot)
        _scheduler.start()
    return _scheduler


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None
