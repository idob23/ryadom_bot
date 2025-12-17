"""
Payment processing service - YooKassa integration.
"""

import uuid
from typing import Optional

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import SubscriptionPlan
from app.db.repository import PaymentRepository, SubscriptionRepository

logger = structlog.get_logger()


class PaymentService:
    """Service for handling payments via YooKassa."""

    YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"

    def __init__(self, session: AsyncSession):
        self.session = session
        self.payment_repo = PaymentRepository(session)
        self.subscription_repo = SubscriptionRepository(session)

    async def create_payment(
        self,
        user_id: int,
        plan: SubscriptionPlan,
        amount: int,
        currency: str = "RUB",
    ) -> str:
        """
        Create payment and return payment URL.

        Args:
            user_id: User ID
            plan: Subscription plan to purchase
            amount: Amount in currency units (e.g., rubles)
            currency: Currency code

        Returns:
            Payment URL for redirect
        """
        idempotency_key = str(uuid.uuid4())

        # Create payment record
        payment = await self.payment_repo.create(
            user_id=user_id,
            amount=amount,
            plan=plan.value,
            provider="yookassa",
        )

        # Prepare YooKassa request
        payload = {
            "amount": {
                "value": str(amount),
                "currency": currency,
            },
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://t.me/{settings.bot_token.split(':')[0]}",  # Return to bot
            },
            "capture": True,
            "description": f"Подписка Рядом: {plan.value}",
            "metadata": {
                "user_id": user_id,
                "plan": plan.value,
                "payment_id": payment.id,
            },
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.YOOKASSA_API_URL,
                    json=payload,
                    auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
                    headers={
                        "Idempotence-Key": idempotency_key,
                        "Content-Type": "application/json",
                    },
                    timeout=30.0,
                )

                if response.status_code != 200:
                    logger.error(
                        "YooKassa API error",
                        status_code=response.status_code,
                        response=response.text,
                    )
                    raise Exception(f"YooKassa API error: {response.status_code}")

                data = response.json()

                # Update payment with external ID
                payment.external_id = data["id"]
                await self.session.flush()

                # Return confirmation URL
                return data["confirmation"]["confirmation_url"]

        except httpx.RequestError as e:
            logger.error("YooKassa request error", error=str(e))
            raise Exception(f"Payment request failed: {e}")

    async def process_webhook(self, webhook_data: dict) -> bool:
        """
        Process YooKassa webhook notification.

        Args:
            webhook_data: Webhook payload from YooKassa

        Returns:
            True if processed successfully
        """
        event_type = webhook_data.get("event")
        payment_data = webhook_data.get("object", {})
        external_id = payment_data.get("id")

        logger.info(
            "Processing payment webhook",
            event=event_type,
            external_id=external_id,
        )

        if event_type != "payment.succeeded":
            # We only care about successful payments
            return True

        # Find payment by external ID
        payment = await self.payment_repo.get_by_external_id(external_id)
        if not payment:
            logger.warning(
                "Payment not found for webhook",
                external_id=external_id,
            )
            return False

        # Mark payment as succeeded
        await self.payment_repo.mark_succeeded(payment.id)

        # Activate subscription
        plan = SubscriptionPlan(payment.plan)
        await self.subscription_repo.upgrade(
            user_id=payment.user_id,
            plan=plan,
            duration_days=30,
        )

        logger.info(
            "Subscription activated via webhook",
            user_id=payment.user_id,
            plan=plan.value,
        )

        return True

    async def check_payment_status(self, external_id: str) -> Optional[str]:
        """
        Check payment status via YooKassa API.

        Returns:
            Payment status: pending, succeeded, canceled, etc.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.YOOKASSA_API_URL}/{external_id}",
                    auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("status")

                return None

        except Exception as e:
            logger.error("Failed to check payment status", error=str(e))
            return None
