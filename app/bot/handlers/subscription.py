"""
Subscription management handlers.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.config import settings
from app.db.models import SubscriptionPlan
from app.db.repository import SubscriptionRepository, UserRepository
from app.services.payments import PaymentService

logger = structlog.get_logger()
router = Router()


def get_subscription_keyboard() -> InlineKeyboardMarkup:
    """Get subscription plans keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Базовый — {settings.basic_price}₽/мес",
                callback_data="subscribe:basic",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"Премиум — {settings.premium_price}₽/мес",
                callback_data="subscribe:premium",
            )
        ],
    ])


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, session: AsyncSession):
    """Handle /subscribe command."""
    telegram_id = message.from_user.id

    user_repo = UserRepository(session)
    subscription_repo = SubscriptionRepository(session)

    user = await user_repo.get_by_telegram_id(telegram_id)
    if not user:
        await message.answer("Для начала напиши /start")
        return

    subscription = await subscription_repo.get_by_user_id(user.id)
    current_plan = subscription.plan if subscription else SubscriptionPlan.FREE.value

    if current_plan == SubscriptionPlan.PREMIUM.value:
        await message.answer(
            "У тебя уже Премиум подписка.\n\n"
            "Чтобы отменить автопродление, напиши /cancel"
        )
        return

    text = """Выбери план:

🆓 **Бесплатный** (текущий)
• 10 сообщений в день
• Базовая память

📗 **Базовый** — {basic_price}₽/мес
• 100 сообщений в день
• Расширенная память
• Отслеживание настроения

💎 **Премиум** — {premium_price}₽/мес
• Безлимит сообщений
• Полная память
• Приоритетные ответы
• Ежедневные check-in""".format(
        basic_price=settings.basic_price,
        premium_price=settings.premium_price,
    )

    await message.answer(text, reply_markup=get_subscription_keyboard())


@router.callback_query(F.data.startswith("subscribe:"))
async def handle_subscribe_callback(callback: CallbackQuery, session: AsyncSession):
    """Handle subscription plan selection."""
    telegram_id = callback.from_user.id
    plan_key = callback.data.split(":")[1]

    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(telegram_id)

    if not user:
        await callback.answer("Ошибка. Напиши /start")
        return

    # Map plan key to plan enum and price
    plans = {
        "basic": (SubscriptionPlan.BASIC, settings.basic_price),
        "premium": (SubscriptionPlan.PREMIUM, settings.premium_price),
    }

    if plan_key not in plans:
        await callback.answer("Неизвестный план")
        return

    plan, price = plans[plan_key]

    # Check if payment service is configured
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        # For development - just upgrade directly
        subscription_repo = SubscriptionRepository(session)
        await subscription_repo.upgrade(user.id, plan, duration_days=30)

        await callback.message.edit_text(
            f"Подписка активирована (тестовый режим).\n\n"
            f"План: {plan.value.title()}\n"
            f"Действует 30 дней."
        )
        await callback.answer("Подписка активирована!")

        logger.info(
            "Subscription activated (test mode)",
            user_id=user.id,
            plan=plan.value,
        )
        return

    # Create payment
    try:
        payment_service = PaymentService(session)
        payment_url = await payment_service.create_payment(
            user_id=user.id,
            plan=plan,
            amount=price,
        )

        await callback.message.edit_text(
            f"Для оплаты плана {plan.value.title()} перейди по ссылке:\n\n"
            f"{payment_url}\n\n"
            f"После оплаты подписка активируется автоматически."
        )
        await callback.answer()

    except Exception as e:
        logger.error(
            "Failed to create payment",
            user_id=user.id,
            error=str(e),
        )
        await callback.answer("Ошибка создания платежа. Попробуй позже.")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, session: AsyncSession):
    """Handle /cancel command - cancel subscription auto-renewal."""
    telegram_id = message.from_user.id

    user_repo = UserRepository(session)
    subscription_repo = SubscriptionRepository(session)

    user = await user_repo.get_by_telegram_id(telegram_id)
    if not user:
        await message.answer("Для начала напиши /start")
        return

    subscription = await subscription_repo.get_by_user_id(user.id)

    if not subscription or subscription.plan == SubscriptionPlan.FREE.value:
        await message.answer("У тебя бесплатный план — отменять нечего.")
        return

    if not subscription.auto_renew:
        await message.answer(
            "Автопродление уже отключено.\n"
            f"Подписка действует до {subscription.expires_at.strftime('%d.%m.%Y')}"
        )
        return

    await subscription_repo.cancel(user.id)

    await message.answer(
        "Автопродление отключено.\n\n"
        f"Подписка будет действовать до {subscription.expires_at.strftime('%d.%m.%Y')}, "
        "после этого переключится на бесплатный план.\n\n"
        "Если передумаешь — просто оформи подписку заново через /subscribe"
    )

    logger.info(
        "Subscription cancelled",
        user_id=user.id,
        plan=subscription.plan,
    )
