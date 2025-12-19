"""
Admin and feedback handlers.

Admin commands for monitoring and managing the bot.
Feedback collection from beta testers.
"""

from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.config import settings
from app.db.models import (
    Feedback,
    Memory,
    Message as MessageModel,
    MoodEntry,
    Person,
    User,
)
from app.db.repository import UserRepository

logger = structlog.get_logger()
router = Router()


def is_admin(telegram_id: int) -> bool:
    """Check if user is admin."""
    return telegram_id in settings.admin_telegram_ids


# ============================================
# FEEDBACK COMMANDS
# ============================================

@router.message(Command("feedback"))
async def cmd_feedback(message: Message, session: AsyncSession):
    """Handle /feedback command - collect user feedback."""
    telegram_id = message.from_user.id

    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(telegram_id)

    if not user:
        await message.answer("Для начала напиши /start")
        return

    # Check if feedback text provided
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        feedback_text = parts[1]

        # Save feedback
        feedback = Feedback(
            user_id=user.id,
            text=feedback_text,
            category="general",
        )
        session.add(feedback)
        await session.flush()

        await message.answer(
            "Спасибо за отзыв! Это очень важно для нас.\n\n"
            "Если хочешь оценить бота от 1 до 5, напиши:\n"
            "/rate 5"
        )

        # Notify admins
        await notify_admins_feedback(message.bot, user, feedback_text)

        logger.info(
            "Feedback received",
            user_id=user.id,
            text_length=len(feedback_text),
        )
        return

    # Show feedback prompt
    await message.answer(
        "Расскажи, что думаешь о боте?\n\n"
        "Напиши свой отзыв после команды:\n"
        "/feedback Мне нравится, что бот помнит наши разговоры\n\n"
        "Или оцени от 1 до 5:\n"
        "/rate 4"
    )


@router.message(Command("rate"))
async def cmd_rate(message: Message, session: AsyncSession):
    """Handle /rate command - rate the bot."""
    telegram_id = message.from_user.id

    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(telegram_id)

    if not user:
        await message.answer("Для начала напиши /start")
        return

    parts = message.text.split()
    if len(parts) > 1:
        try:
            rating = int(parts[1])
            if 1 <= rating <= 5:
                feedback = Feedback(
                    user_id=user.id,
                    rating=rating,
                    category="rating",
                )
                session.add(feedback)
                await session.flush()

                stars = "⭐" * rating
                await message.answer(
                    f"Спасибо за оценку! {stars}\n\n"
                    "Если хочешь добавить комментарий:\n"
                    "/feedback твой текст"
                )

                logger.info("Rating received", user_id=user.id, rating=rating)
                return
            else:
                await message.answer("Укажи число от 1 до 5")
                return
        except ValueError:
            pass

    await message.answer(
        "Оцени бота от 1 до 5:\n\n"
        "/rate 5 — отлично\n"
        "/rate 4 — хорошо\n"
        "/rate 3 — нормально\n"
        "/rate 2 — плохо\n"
        "/rate 1 — ужасно"
    )


@router.message(Command("bug"))
async def cmd_bug(message: Message, session: AsyncSession):
    """Handle /bug command - report a bug."""
    telegram_id = message.from_user.id

    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(telegram_id)

    if not user:
        await message.answer("Для начала напиши /start")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        bug_text = parts[1]

        feedback = Feedback(
            user_id=user.id,
            text=bug_text,
            category="bug",
        )
        session.add(feedback)
        await session.flush()

        await message.answer(
            "Спасибо за сообщение об ошибке! Мы исправим это."
        )

        # Notify admins about bug
        await notify_admins_bug(message.bot, user, bug_text)

        logger.warning("Bug reported", user_id=user.id, text=bug_text[:100])
        return

    await message.answer(
        "Опиши ошибку после команды:\n"
        "/bug Бот не отвечает на сообщения"
    )


async def notify_admins_feedback(bot: Bot, user: User, text: str):
    """Notify admins about new feedback."""
    if not settings.admin_telegram_ids:
        return

    message = (
        f"📝 Новый отзыв\n\n"
        f"От: {user.name or 'Без имени'} (ID: {user.telegram_id})\n"
        f"Текст: {text[:500]}"
    )

    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.send_message(admin_id, message)
        except Exception as e:
            logger.error("Failed to notify admin", admin_id=admin_id, error=str(e))


async def notify_admins_bug(bot: Bot, user: User, text: str):
    """Notify admins about bug report."""
    if not settings.admin_telegram_ids:
        return

    message = (
        f"🐛 Сообщение об ошибке\n\n"
        f"От: {user.name or 'Без имени'} (ID: {user.telegram_id})\n"
        f"Текст: {text[:500]}"
    )

    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.send_message(admin_id, message)
        except Exception as e:
            logger.error("Failed to notify admin", admin_id=admin_id, error=str(e))


# ============================================
# ADMIN COMMANDS
# ============================================

@router.message(Command("admin"))
async def cmd_admin(message: Message, session: AsyncSession):
    """Handle /admin command - show admin panel."""
    if not is_admin(message.from_user.id):
        return  # Silently ignore for non-admins

    await message.answer(
        "🔧 Админ-панель\n\n"
        "/stats — статистика бота\n"
        "/users — список пользователей\n"
        "/user [id] — информация о пользователе\n"
        "/feedbacks — последние отзывы\n"
        "/broadcast [текст] — рассылка всем\n"
        "/message [user_id] [текст] — сообщение пользователю"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession):
    """Handle /stats command - show bot statistics."""
    if not is_admin(message.from_user.id):
        return

    # Total users
    total_users = await session.scalar(select(func.count(User.id)))

    # Active users (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    active_users = await session.scalar(
        select(func.count(User.id)).where(User.last_active_at >= week_ago)
    )

    # Today's active
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_active = await session.scalar(
        select(func.count(User.id)).where(User.last_active_at >= today)
    )

    # Total messages
    total_messages = await session.scalar(select(func.count(MessageModel.id)))

    # Today's messages
    today_messages = await session.scalar(
        select(func.count(MessageModel.id)).where(MessageModel.created_at >= today)
    )

    # Memories count
    total_memories = await session.scalar(select(func.count(Memory.id)))

    # Persons count
    total_persons = await session.scalar(select(func.count(Person.id)))

    # Average mood
    avg_mood = await session.scalar(
        select(func.avg(MoodEntry.mood_score)).where(MoodEntry.created_at >= week_ago)
    )

    # Crisis alerts (requires_attention)
    crisis_count = await session.scalar(
        select(func.count(MoodEntry.id)).where(
            MoodEntry.requires_attention == True,
            MoodEntry.created_at >= week_ago,
        )
    )

    # Feedbacks
    total_feedbacks = await session.scalar(select(func.count(Feedback.id)))
    avg_rating = await session.scalar(
        select(func.avg(Feedback.rating)).where(Feedback.rating.isnot(None))
    )

    avg_mood_str = f"{avg_mood:.1f}/10" if avg_mood else "N/A"
    avg_rating_str = f"{avg_rating:.1f}/5" if avg_rating else "N/A"

    stats_text = f"""📊 Статистика бота

👥 Пользователи:
• Всего: {total_users}
• Активных (7 дней): {active_users}
• Сегодня: {today_active}

💬 Сообщения:
• Всего: {total_messages}
• Сегодня: {today_messages}

🧠 Память:
• Фактов: {total_memories}
• Людей: {total_persons}

😊 Настроение:
• Среднее (7 дней): {avg_mood_str}
• Кризисных: {crisis_count}

📝 Отзывы:
• Всего: {total_feedbacks}
• Средняя оценка: {avg_rating_str}"""

    await message.answer(stats_text)


@router.message(Command("users"))
async def cmd_users(message: Message, session: AsyncSession):
    """Handle /users command - list recent users."""
    if not is_admin(message.from_user.id):
        return

    # Get last 20 active users
    result = await session.execute(
        select(User)
        .order_by(User.last_active_at.desc().nullslast())
        .limit(20)
    )
    users = result.scalars().all()

    if not users:
        await message.answer("Пользователей пока нет")
        return

    lines = ["👥 Последние пользователи:\n"]
    for u in users:
        last_active = u.last_active_at.strftime("%d.%m %H:%M") if u.last_active_at else "никогда"
        lines.append(
            f"• {u.name or 'Без имени'} (ID: {u.id}, TG: {u.telegram_id})\n"
            f"  Активность: {last_active}"
        )

    await message.answer("\n".join(lines))


@router.message(Command("user"))
async def cmd_user_info(message: Message, session: AsyncSession):
    """Handle /user <id> command - show user details."""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /user [user_id или telegram_id]")
        return

    try:
        user_id = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом")
        return

    # Try to find by user_id or telegram_id
    user_repo = UserRepository(session)
    user = await session.scalar(
        select(User).where((User.id == user_id) | (User.telegram_id == user_id))
    )

    if not user:
        await message.answer("Пользователь не найден")
        return

    # Get user stats
    messages_count = await session.scalar(
        select(func.count(MessageModel.id)).where(MessageModel.user_id == user.id)
    )
    memories_count = await session.scalar(
        select(func.count(Memory.id)).where(Memory.user_id == user.id)
    )
    persons_count = await session.scalar(
        select(func.count(Person.id)).where(Person.user_id == user.id)
    )

    # Recent mood
    recent_mood = await session.scalar(
        select(MoodEntry.mood_score)
        .where(MoodEntry.user_id == user.id)
        .order_by(MoodEntry.created_at.desc())
        .limit(1)
    )

    last_active_str = user.last_active_at.strftime('%d.%m.%Y %H:%M') if user.last_active_at else 'никогда'
    mood_str = f"{recent_mood}/10" if recent_mood else "N/A"

    info_text = f"""👤 Пользователь

ID: {user.id}
Telegram ID: {user.telegram_id}
Имя: {user.name or 'Не указано'}
Создан: {user.created_at.strftime('%d.%m.%Y %H:%M')}
Последняя активность: {last_active_str}

📊 Статистика:
• Сообщений: {messages_count}
• Воспоминаний: {memories_count}
• Людей в жизни: {persons_count}
• Последнее настроение: {mood_str}

Для отправки сообщения:
/message {user.telegram_id} текст"""

    await message.answer(info_text)


@router.message(Command("feedbacks"))
async def cmd_feedbacks(message: Message, session: AsyncSession):
    """Handle /feedbacks command - show recent feedback."""
    if not is_admin(message.from_user.id):
        return

    result = await session.execute(
        select(Feedback)
        .order_by(Feedback.created_at.desc())
        .limit(10)
    )
    feedbacks = result.scalars().all()

    if not feedbacks:
        await message.answer("Отзывов пока нет")
        return

    lines = ["📝 Последние отзывы:\n"]
    for f in feedbacks:
        user = await session.scalar(select(User).where(User.id == f.user_id))
        user_name = user.name if user else "Unknown"

        if f.rating:
            lines.append(f"⭐ {f.rating}/5 от {user_name}")
        if f.text:
            lines.append(f"💬 {user_name}: {f.text[:100]}...")
        lines.append(f"   {f.created_at.strftime('%d.%m %H:%M')} [{f.category}]\n")

    await message.answer("\n".join(lines))


@router.message(Command("message"))
async def cmd_send_message(message: Message, session: AsyncSession):
    """Handle /message <telegram_id> <text> - send message to user."""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Использование: /message [telegram_id] [текст]")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом")
        return

    text = parts[2]

    try:
        await message.bot.send_message(
            target_id,
            f"📢 Сообщение от команды Рядом:\n\n{text}"
        )
        await message.answer(f"✅ Сообщение отправлено пользователю {target_id}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, session: AsyncSession):
    """Handle /broadcast <text> - send message to all users."""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /broadcast [текст]")
        return

    text = parts[1]

    # Get all active users
    result = await session.execute(
        select(User).where(User.is_active == True)
    )
    users = result.scalars().all()

    sent = 0
    failed = 0

    status_msg = await message.answer(f"Отправляю {len(users)} пользователям...")

    for user in users:
        try:
            await message.bot.send_message(
                user.telegram_id,
                f"📢 {text}"
            )
            sent += 1
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ Рассылка завершена\n"
        f"Отправлено: {sent}\n"
        f"Ошибок: {failed}"
    )


# ============================================
# CRISIS NOTIFICATION
# ============================================

async def notify_admins_crisis(
    bot: Bot,
    user: User,
    indicators: list[str],
    message_text: str,
):
    """Notify admins about crisis situation."""
    if not settings.admin_telegram_ids:
        logger.warning("No admin IDs configured for crisis notification")
        return

    alert = (
        f"🚨 КРИЗИСНАЯ СИТУАЦИЯ\n\n"
        f"Пользователь: {user.name or 'Без имени'}\n"
        f"Telegram ID: {user.telegram_id}\n"
        f"User ID: {user.id}\n\n"
        f"Индикаторы: {', '.join(indicators) if indicators else 'не указаны'}\n\n"
        f"Сообщение: {message_text[:500]}\n\n"
        f"Для связи: /message {user.telegram_id} [текст]"
    )

    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.send_message(admin_id, alert)
            logger.info("Crisis alert sent to admin", admin_id=admin_id)
        except Exception as e:
            logger.error("Failed to send crisis alert", admin_id=admin_id, error=str(e))
