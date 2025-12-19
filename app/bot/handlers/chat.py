"""
Main chat handler - the core conversation flow.

This is where the magic happens. Every message goes through here
and this determines whether the user feels heard or processed.
"""

import random

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.bot.handlers.admin import notify_admins_crisis
from app.core.claude import get_claude_client, ClaudeAPIError
from app.core.memory import MemoryManager
from app.core.prompts import (
    ONBOARDING_PROMPTS,
    get_crisis_response,
    build_additional_context,
)
from app.config import settings
from app.db.repository import (
    MessageRepository,
    MoodRepository,
    SubscriptionRepository,
    UserRepository,
    UsageLogRepository,
)

logger = structlog.get_logger()
router = Router()


def extract_name(text: str) -> str | None:
    """
    Extract name from user's message.
    Returns None if no valid name found (just greeting or garbage).
    """
    skip_words = {
        # Greetings
        'привет', 'здравствуй', 'здравствуйте', 'хай', 'хей', 'hello', 'hi',
        'добрый', 'доброе', 'добрая', 'утро', 'день', 'вечер', 'ночь',
        # Filler words
        'я', 'меня', 'зовут', 'это', 'мое', 'моё', 'имя', 'можешь', 'называть',
        'звать', 'ну', 'так', 'вот', 'ага', 'да', 'нет', 'ок', 'окей', 'ладно',
        'мне', 'тебя', 'как', 'что', 'кто',
    }
    words = text.strip().split()
    for word in words:
        clean_word = word.lower().strip('.,!?-')
        # Skip if it's a stop word or too short
        if clean_word in skip_words or len(clean_word) <= 1:
            continue
        # Skip if it looks like a sentence, not a name
        if len(words) > 5:
            continue
        # Return with original capitalization
        return word.strip('.,!?-').capitalize()
    # No valid name found
    return None


def get_returning_prompt(user, days_since: int, last_mood: dict = None) -> str:
    """
    Get appropriate returning user prompt based on context.

    - If they were having a hard time last time, acknowledge it
    - If it's been a long time, note that
    - Otherwise, just a simple greeting
    """
    if last_mood and last_mood.get("mood_score", 5) <= 3:
        return ONBOARDING_PROMPTS["returning_after_heavy"].format(name=user.name)

    if days_since > 7:
        return ONBOARDING_PROMPTS["returning_after_break"].format(name=user.name)

    return ONBOARDING_PROMPTS["returning_short"].format(name=user.name)


@router.message(F.text)
async def handle_message(message: Message, session: AsyncSession):
    """
    Main message handler.

    Each message goes through:
    1. User lookup/creation
    2. Rate limit check
    3. Mood detection (what are they feeling?)
    4. Memory management (what should we remember?)
    5. Context building (what do we know about them?)
    6. Response generation (how should we respond?)
    7. Post-processing (summarize if needed)
    """
    telegram_id = message.from_user.id
    user_text = message.text.strip()

    # Initialize repositories
    user_repo = UserRepository(session)
    message_repo = MessageRepository(session)
    subscription_repo = SubscriptionRepository(session)
    usage_repo = UsageLogRepository(session)
    mood_repo = MoodRepository(session)
    memory_manager = MemoryManager(session)

    # Get or create user
    user, is_new = await user_repo.get_or_create(telegram_id)

    logger.info(
        "Message received",
        telegram_id=telegram_id,
        user_id=user.id,
        is_new=is_new,
        message_length=len(user_text),
    )

    # Check rate limit (admins have no limit)
    is_admin = telegram_id in settings.admin_telegram_ids

    if not is_admin:
        daily_limit = await subscription_repo.get_plan_limit(user.id)
        messages_today = await message_repo.get_messages_count_today(user.id)

        if messages_today >= daily_limit:
            await message.answer(
                f"На сегодня лимит сообщений исчерпан ({daily_limit}).\n\n"
                "Я буду рад продолжить завтра, или ты можешь расширить лимит "
                "с помощью /subscribe"
            )
            return

    # Save user message
    await message_repo.save(user.id, "user", user_text)

    # Handle onboarding - collecting name
    if not user.name:
        name = extract_name(user_text)

        # If no name found, just use "Друг" and move on
        if name is None:
            name = "Друг"

        await user_repo.update_name(user.id, name)
        await user_repo.complete_onboarding(user.id)
        # Refresh user object with new name
        user.name = name

        logger.info("User onboarded", user_id=user.id, name=name)
        # Don't return - let the message go to Claude for a natural response

    # Process message for mood and memories
    process_result = await memory_manager.process_message(
        user.id, user_text, role="user"
    )

    # Check if requires crisis response
    if process_result.get("requires_attention"):
        response = get_crisis_response()
        await message_repo.save(user.id, "assistant", response)
        await message.answer(response)

        # Notify admins about crisis situation
        await notify_admins_crisis(
            message.bot,
            user,
            process_result.get("attention_reason", []),
            user_text,
        )

        logger.warning(
            "Crisis response sent",
            user_id=user.id,
            reason=process_result.get("attention_reason"),
        )
        return

    # Get FULL context for response (all memories, persons, events)
    context = await memory_manager.get_relevant_context(
        user_id=user.id,
        current_message=user_text,
        user_data={
            "name": user.name,
            "profile": user.profile,
            "preferences": user.preferences,
            "last_active_at": user.last_active_at,
        },
    )

    # Build additional context with current mood
    additional_context = build_additional_context(
        time_of_day=context["time_of_day"],
        days_since_last_chat=context["days_since_last_chat"],
        conversation_summaries=context["summaries"],
        current_mood=context.get("current_mood"),
    )

    # Generate response
    try:
        claude = get_claude_client()
        response_data = await claude.get_response(
            user_data={
                "name": user.name,
                "profile": user.profile or {},
                "preferences": user.preferences or {},
            },
            messages=context["messages"],
            memories=context["all_memories"],
            mood_history=context["mood_history"],
            conversation_summaries=context["summaries"],
            time_of_day=context["time_of_day"],
            days_since_last_chat=context["days_since_last_chat"],
            persons=context["persons"],
            recent_events=context["recent_events"],
            upcoming_dates=context["upcoming_dates"],
        )

        response = response_data.content

        # Save response
        await message_repo.save(
            user.id,
            "assistant",
            response,
            tokens_used=response_data.tokens_input + response_data.tokens_output,
            response_time_ms=response_data.response_time_ms,
        )

        # Update usage stats
        await usage_repo.increment(
            user.id,
            messages=1,
            tokens=response_data.tokens_input + response_data.tokens_output,
        )

        # Update last active
        await user_repo.update_last_active(user.id)

        logger.info(
            "Response generated",
            user_id=user.id,
            tokens=response_data.tokens_input + response_data.tokens_output,
            response_time_ms=response_data.response_time_ms,
            emotion=process_result.get("primary_emotion"),
        )

    except Exception as e:
        error_type = type(e).__name__
        logger.error(
            "Failed to generate response",
            user_id=user.id,
            error=str(e),
            error_type=error_type,
        )

        # Different fallbacks for different error types
        if isinstance(e, ClaudeAPIError):
            if e.status_code == 429:
                # Rate limited - be honest but reassuring
                response = (
                    f"{user.name}, сейчас много обращений и мне нужна минутка. "
                    "Напиши чуть позже — я обязательно отвечу. 💙"
                )
            elif e.status_code and e.status_code >= 500:
                # Server error
                response = (
                    f"Ой, {user.name}, что-то пошло не так с моей стороны. "
                    "Попробуй написать ещё раз через минуту?"
                )
            else:
                # Other API errors
                response = (
                    f"Прости, {user.name}, возникла техническая заминка. "
                    "Я всё ещё здесь — попробуй написать ещё раз."
                )
        else:
            # General fallback - still be present and warm
            fallback_responses = [
                f"Я тебя слышу, {user.name}. Расскажи больше.",
                f"Продолжай, {user.name}. Я здесь.",
                f"Понимаю. Что ещё ты хочешь сказать?",
            ]
            response = random.choice(fallback_responses)

        await message_repo.save(user.id, "assistant", response)

    await message.answer(response)

    # Check if we need to summarize conversation
    if await memory_manager.should_summarize(user.id):
        await memory_manager.create_summary(user.id)
