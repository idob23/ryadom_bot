"""
Proactive check-in service.

This is what makes the bot feel like it actually CARES about the user.
Instead of just waiting for them to write, it reaches out:
- When they haven't been around for a while
- After a particularly hard conversation
- To follow up on something they mentioned
"""

from datetime import datetime, timedelta
from typing import Optional

import structlog
from aiogram import Bot
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.claude import get_claude_client
from app.core.prompts import PROACTIVE_CHECKIN_PROMPT
from app.db.models import User, MoodEntry, ConversationSummary
from app.db.repository import (
    ConversationSummaryRepository,
    MemoryRepository,
    MoodRepository,
    UserRepository,
)

logger = structlog.get_logger()


class ProactiveService:
    """
    Service for sending proactive check-in messages.

    The goal: make the user feel like someone genuinely cares
    about how they're doing, not like they're getting spam.
    """

    def __init__(self, session: AsyncSession, bot: Bot):
        self.session = session
        self.bot = bot
        self.claude = get_claude_client()

    async def get_users_to_checkin(
        self,
        min_days_inactive: int = 3,
        max_users: int = 50,
    ) -> list[User]:
        """
        Find users who might benefit from a check-in.

        Criteria:
        - Haven't been active for min_days_inactive days
        - Had meaningful conversations before (not just /start)
        - Not blocked
        - Have name (completed onboarding)
        - Have proactive check-ins enabled (or not set = default True)
        """
        cutoff = datetime.utcnow() - timedelta(days=min_days_inactive)

        result = await self.session.execute(
            select(User)
            .where(
                and_(
                    User.last_active_at < cutoff,
                    User.last_active_at.isnot(None),
                    User.is_active == True,
                    User.is_blocked == False,
                    User.name.isnot(None),
                    User.onboarding_completed == True,
                )
            )
            .order_by(User.last_active_at.asc())
            .limit(max_users)
        )
        users = list(result.scalars().all())

        # Filter by preferences (check if proactive_checkins is not disabled)
        return [
            u for u in users
            if (u.preferences or {}).get("proactive_checkins", True)
        ]

    async def generate_checkin_message(self, user: User) -> Optional[str]:
        """
        Generate a personalized check-in message for a user.

        The message should:
        - Feel natural, not automated
        - Reference something from past conversations if possible
        - Not be pushy
        - Give them an easy way to respond or not
        """
        # Get context
        memory_repo = MemoryRepository(self.session)
        mood_repo = MoodRepository(self.session)
        summary_repo = ConversationSummaryRepository(self.session)

        memories = await memory_repo.get_all(user.id)
        last_mood = await mood_repo.get_recent(user.id, days=30)
        summaries = await summary_repo.get_recent(user.id, limit=1)

        # Build user context
        user_context = f"Имя: {user.name}"
        if memories:
            important_memories = [m for m in memories if m.importance >= 6][:5]
            if important_memories:
                user_context += "\n\nВажные факты:"
                for m in important_memories:
                    user_context += f"\n- {m.fact}"

        # Last summary
        last_summary = summaries[0].summary if summaries else "Разговоров пока было мало"

        # Last mood
        last_mood_info = "неизвестно"
        if last_mood:
            score = last_mood[0].mood_score
            emotion = last_mood[0].primary_emotion or ""
            if score <= 3:
                last_mood_info = f"низкое ({emotion})" if emotion else "низкое"
            elif score <= 6:
                last_mood_info = f"среднее ({emotion})" if emotion else "среднее"
            else:
                last_mood_info = f"хорошее ({emotion})" if emotion else "хорошее"

        # Days since last chat
        days_since = 0
        if user.last_active_at:
            days_since = (datetime.utcnow() - user.last_active_at).days

        # Time of day
        hour = datetime.now().hour
        if 5 <= hour < 12:
            time_of_day = "утро"
        elif 12 <= hour < 17:
            time_of_day = "день"
        elif 17 <= hour < 22:
            time_of_day = "вечер"
        else:
            time_of_day = "ночь"

        # Generate message
        prompt = PROACTIVE_CHECKIN_PROMPT.format(
            user_context=user_context,
            last_summary=last_summary,
            days_since=days_since,
            time_of_day=time_of_day,
            last_mood=last_mood_info,
        )

        try:
            response = await self.claude._make_request(
                messages=[{"role": "user", "content": prompt}],
                system="Ты — Рядом, эмоциональный компаньон. Создай короткое, тёплое сообщение.",
                max_tokens=150,
                use_fast_model=True,  # Haiku for check-ins
            )
            return response.content.strip().strip('"')

        except Exception as e:
            logger.error("Failed to generate check-in", error=str(e))
            return None

    async def send_checkin(self, user: User) -> bool:
        """
        Send a check-in message to a user.

        Returns True if sent successfully.
        """
        message = await self.generate_checkin_message(user)
        if not message:
            return False

        try:
            await self.bot.send_message(
                chat_id=user.telegram_id,
                text=message,
            )

            logger.info(
                "Sent check-in message",
                user_id=user.id,
                telegram_id=user.telegram_id,
            )
            return True

        except Exception as e:
            # User might have blocked the bot
            logger.warning(
                "Failed to send check-in",
                user_id=user.id,
                error=str(e),
            )
            return False

    async def run_checkins(self, min_days: int = 3, max_users: int = 20) -> int:
        """
        Run batch of check-ins.

        Returns number of messages sent.
        """
        users = await self.get_users_to_checkin(min_days, max_users)

        sent_count = 0
        for user in users:
            success = await self.send_checkin(user)
            if success:
                sent_count += 1
                # Update last_active so we don't spam them
                user.last_active_at = datetime.utcnow()
                await self.session.flush()

        logger.info(
            "Check-in batch completed",
            found=len(users),
            sent=sent_count,
        )

        return sent_count


async def should_followup_after_crisis(session: AsyncSession, user_id: int) -> bool:
    """
    Check if we should follow up with a user after a crisis situation.

    Rules:
    - Last mood entry had requires_attention=True
    - It's been more than 2 hours since then
    - Less than 24 hours (so not too old)
    """
    mood_repo = MoodRepository(session)
    entries = await mood_repo.get_recent(user_id, days=1)

    if not entries:
        return False

    latest = entries[0]
    if not latest.requires_attention:
        return False

    hours_since = (datetime.utcnow() - latest.created_at).total_seconds() / 3600

    return 2 <= hours_since <= 24
