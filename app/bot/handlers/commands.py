"""
Command handlers - /start, /help, /mood, /status, etc.
"""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.core.prompts import ONBOARDING_PROMPTS, get_crisis_response
from app.db.repository import (
    MessageRepository,
    MoodRepository,
    SubscriptionRepository,
    UserRepository,
)
from app.db.models import SubscriptionPlan

logger = structlog.get_logger()
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession):
    """Handle /start command."""
    telegram_id = message.from_user.id

    user_repo = UserRepository(session)
    message_repo = MessageRepository(session)

    user, is_new = await user_repo.get_or_create(telegram_id)

    if is_new or not user.name:
        # New user - start onboarding
        response = ONBOARDING_PROMPTS["welcome"]
    else:
        # Returning user
        response = ONBOARDING_PROMPTS["returning_user"].format(name=user.name)

    await message_repo.save(user.id, "assistant", response)
    await message.answer(response)

    logger.info(
        "Start command",
        telegram_id=telegram_id,
        is_new=is_new,
    )


@router.message(Command("help"))
async def cmd_help(message: Message, session: AsyncSession):
    """Handle /help command."""
    help_text = """Я — Рядом. Просто тот, кто слушает.

Команды:
/start — начать сначала
/status — статус аккаунта
/mood — отметить настроение
/subscribe — управление подпиской
/crisis — экстренная помощь
/help — эта справка

Можешь просто писать мне — я всегда готов выслушать."""

    await message.answer(help_text)


@router.message(Command("status"))
async def cmd_status(message: Message, session: AsyncSession):
    """Handle /status command - show account status."""
    telegram_id = message.from_user.id

    user_repo = UserRepository(session)
    subscription_repo = SubscriptionRepository(session)
    message_repo = MessageRepository(session)

    user = await user_repo.get_by_telegram_id(telegram_id)
    if not user:
        await message.answer("Для начала напиши /start")
        return

    subscription = await subscription_repo.get_by_user_id(user.id)
    messages_today = await message_repo.get_messages_count_today(user.id)
    daily_limit = await subscription_repo.get_plan_limit(user.id)

    plan_names = {
        SubscriptionPlan.FREE.value: "Бесплатный",
        SubscriptionPlan.BASIC.value: "Базовый",
        SubscriptionPlan.PREMIUM.value: "Премиум",
    }

    plan_name = plan_names.get(subscription.plan if subscription else "free", "Бесплатный")

    status_text = f"""Твой аккаунт

Имя: {user.name or 'Не указано'}
План: {plan_name}
Сообщений сегодня: {messages_today}/{daily_limit}"""

    if subscription and subscription.expires_at:
        status_text += f"\nДействует до: {subscription.expires_at.strftime('%d.%m.%Y')}"

    await message.answer(status_text)


@router.message(Command("mood"))
async def cmd_mood(message: Message, session: AsyncSession):
    """Handle /mood command - let user record mood."""
    telegram_id = message.from_user.id

    user_repo = UserRepository(session)
    mood_repo = MoodRepository(session)

    user = await user_repo.get_by_telegram_id(telegram_id)
    if not user:
        await message.answer("Для начала напиши /start")
        return

    # Check for mood argument: /mood 7
    parts = message.text.split()
    if len(parts) > 1:
        try:
            mood_score = int(parts[1])
            if 1 <= mood_score <= 10:
                await mood_repo.add(
                    user_id=user.id,
                    mood_score=mood_score,
                    source="manual",
                )
                await message.answer(f"Записал: настроение {mood_score}/10")

                # Get weekly average
                avg_mood = await mood_repo.get_average_mood(user.id, days=7)
                if avg_mood:
                    await message.answer(f"Среднее за неделю: {avg_mood:.1f}/10")
                return
            else:
                await message.answer("Укажи число от 1 до 10")
                return
        except ValueError:
            pass

    # Show mood tracking prompt
    recent_moods = await mood_repo.get_recent(user.id, days=7)
    avg_mood = await mood_repo.get_average_mood(user.id, days=7)

    mood_text = "Как ты себя чувствуешь сейчас?\n\nОтправь число от 1 до 10:\n"
    mood_text += "1-3 — тяжело\n4-6 — нормально\n7-10 — хорошо\n"
    mood_text += "\nНапример: /mood 6"

    if avg_mood:
        mood_text += f"\n\nТвоё среднее за неделю: {avg_mood:.1f}/10"
        mood_text += f" ({len(recent_moods)} записей)"

    await message.answer(mood_text)


@router.message(Command("crisis"))
async def cmd_crisis(message: Message, session: AsyncSession):
    """Handle /crisis command - show emergency help info."""
    crisis_text = """Если тебе сейчас очень плохо — ты не один.

Телефоны доверия (бесплатно, анонимно, круглосуточно):

📞 8-800-2000-122 — для всех
📞 051 — с мобильного
📞 8-495-051 — Москва

Там выслушают и помогут.

Я тоже здесь, если хочешь поговорить."""

    await message.answer(crisis_text)

    logger.info(
        "Crisis command used",
        telegram_id=message.from_user.id,
    )


@router.message(Command("reset"))
async def cmd_reset(message: Message, session: AsyncSession):
    """Handle /reset command - start fresh (for testing)."""
    # This is mainly for development/testing
    telegram_id = message.from_user.id

    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(telegram_id)

    if user:
        # Just reset onboarding
        await user_repo.update_name(user.id, None)
        await message.answer("Начнём сначала. Напиши /start")
    else:
        await message.answer("Напиши /start для начала")
