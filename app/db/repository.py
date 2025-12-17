from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConversationSummary,
    Memory,
    Message,
    MoodEntry,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    UsageLog,
    User,
)


class UserRepository:
    """Repository for User operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Get user by Telegram ID."""
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def create(self, telegram_id: int, name: Optional[str] = None) -> User:
        """Create new user with default free subscription."""
        user = User(telegram_id=telegram_id, name=name)
        self.session.add(user)
        await self.session.flush()

        # Create default free subscription
        subscription = Subscription(
            user_id=user.id,
            plan=SubscriptionPlan.FREE.value,
            status=SubscriptionStatus.ACTIVE.value,
        )
        self.session.add(subscription)
        await self.session.flush()

        return user

    async def get_or_create(self, telegram_id: int) -> tuple[User, bool]:
        """Get existing user or create new one. Returns (user, created)."""
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            return user, False
        user = await self.create(telegram_id)
        return user, True

    async def update_name(self, user_id: int, name: str) -> None:
        """Update user's name."""
        await self.session.execute(
            update(User).where(User.id == user_id).values(name=name)
        )

    async def update_profile(self, user_id: int, profile: dict) -> None:
        """Update user's profile data."""
        await self.session.execute(
            update(User).where(User.id == user_id).values(profile=profile)
        )

    async def update_preferences(self, user_id: int, preferences: dict) -> None:
        """Update user's preferences."""
        await self.session.execute(
            update(User).where(User.id == user_id).values(preferences=preferences)
        )

    async def complete_onboarding(self, user_id: int) -> None:
        """Mark onboarding as completed."""
        await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(onboarding_completed=True)
        )

    async def update_onboarding_step(self, user_id: int, step: int) -> None:
        """Update onboarding step."""
        await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(onboarding_step=step)
        )

    async def update_last_active(self, user_id: int) -> None:
        """Update last active timestamp."""
        await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_active_at=func.now())
        )


class MessageRepository:
    """Repository for Message operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(
        self,
        user_id: int,
        role: str,
        content: str,
        tokens_used: Optional[int] = None,
        response_time_ms: Optional[int] = None,
    ) -> Message:
        """Save a message."""
        message = Message(
            user_id=user_id,
            role=role,
            content=content,
            tokens_used=tokens_used,
            response_time_ms=response_time_ms,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def get_recent(self, user_id: int, limit: int = 20) -> list[Message]:
        """Get recent messages for context."""
        result = await self.session.execute(
            select(Message)
            .where(Message.user_id == user_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        return list(reversed(messages))  # Return in chronological order

    async def get_messages_count_today(self, user_id: int) -> int:
        """Get count of user's messages today."""
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self.session.execute(
            select(func.count(Message.id))
            .where(
                and_(
                    Message.user_id == user_id,
                    Message.role == "user",
                    Message.created_at >= today_start,
                )
            )
        )
        return result.scalar() or 0

    async def mark_as_summarized(self, message_ids: list[int]) -> None:
        """Mark messages as summarized."""
        await self.session.execute(
            update(Message)
            .where(Message.id.in_(message_ids))
            .values(is_summarized=True)
        )


class MemoryRepository:
    """Repository for Memory operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        user_id: int,
        fact: str,
        category: str = "general",
        importance: int = 5,
        emotional_weight: str = "neutral",
        source_message_id: Optional[int] = None,
    ) -> Memory:
        """Add a memory fact."""
        memory = Memory(
            user_id=user_id,
            fact=fact,
            category=category,
            importance=importance,
            emotional_weight=emotional_weight,
            source_message_id=source_message_id,
        )
        self.session.add(memory)
        await self.session.flush()
        return memory

    async def get_all(self, user_id: int) -> list[Memory]:
        """Get all memories for a user."""
        result = await self.session.execute(
            select(Memory)
            .where(Memory.user_id == user_id)
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_category(self, user_id: int, category: str) -> list[Memory]:
        """Get memories by category."""
        result = await self.session.execute(
            select(Memory)
            .where(and_(Memory.user_id == user_id, Memory.category == category))
            .order_by(Memory.importance.desc())
        )
        return list(result.scalars().all())

    async def get_important(self, user_id: int, min_importance: int = 7) -> list[Memory]:
        """Get important memories."""
        result = await self.session.execute(
            select(Memory)
            .where(
                and_(Memory.user_id == user_id, Memory.importance >= min_importance)
            )
            .order_by(Memory.importance.desc())
        )
        return list(result.scalars().all())

    async def update_importance(self, memory_id: int, importance: int) -> None:
        """Update memory importance."""
        await self.session.execute(
            update(Memory)
            .where(Memory.id == memory_id)
            .values(importance=importance, last_accessed_at=func.now())
        )


class SubscriptionRepository:
    """Repository for Subscription operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_id(self, user_id: int) -> Optional[Subscription]:
        """Get user's subscription."""
        result = await self.session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def upgrade(
        self,
        user_id: int,
        plan: SubscriptionPlan,
        duration_days: int = 30,
    ) -> Subscription:
        """Upgrade user's subscription."""
        subscription = await self.get_by_user_id(user_id)
        if not subscription:
            subscription = Subscription(user_id=user_id)
            self.session.add(subscription)

        subscription.plan = plan.value
        subscription.status = SubscriptionStatus.ACTIVE.value
        subscription.started_at = datetime.utcnow()
        subscription.expires_at = datetime.utcnow() + timedelta(days=duration_days)
        subscription.cancelled_at = None

        await self.session.flush()
        return subscription

    async def cancel(self, user_id: int) -> None:
        """Cancel subscription (will expire at end of period)."""
        await self.session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(
                auto_renew=False,
                cancelled_at=func.now(),
            )
        )

    async def check_and_expire(self) -> int:
        """Check and expire subscriptions. Returns count of expired."""
        now = datetime.utcnow()
        result = await self.session.execute(
            update(Subscription)
            .where(
                and_(
                    Subscription.expires_at < now,
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                    Subscription.plan != SubscriptionPlan.FREE.value,
                )
            )
            .values(
                status=SubscriptionStatus.EXPIRED.value,
                plan=SubscriptionPlan.FREE.value,
            )
        )
        return result.rowcount

    async def get_plan_limit(self, user_id: int) -> int:
        """Get daily message limit for user's plan."""
        from app.config import settings

        subscription = await self.get_by_user_id(user_id)
        if not subscription:
            return settings.free_messages_per_day

        # Check if subscription is active and not expired
        if subscription.status != SubscriptionStatus.ACTIVE.value:
            return settings.free_messages_per_day

        if subscription.expires_at and subscription.expires_at < datetime.utcnow():
            return settings.free_messages_per_day

        limits = {
            SubscriptionPlan.FREE.value: settings.free_messages_per_day,
            SubscriptionPlan.BASIC.value: settings.basic_messages_per_day,
            SubscriptionPlan.PREMIUM.value: settings.premium_messages_per_day,
        }
        return limits.get(subscription.plan, settings.free_messages_per_day)


class MoodRepository:
    """Repository for MoodEntry operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        user_id: int,
        mood_score: int,
        energy_level: Optional[int] = None,
        anxiety_level: Optional[int] = None,
        primary_emotion: Optional[str] = None,
        secondary_emotions: Optional[list] = None,
        emotional_need: Optional[str] = None,
        note: Optional[str] = None,
        source: str = "auto",
        requires_attention: bool = False,
    ) -> MoodEntry:
        """Add mood entry with full emotional data."""
        entry = MoodEntry(
            user_id=user_id,
            mood_score=mood_score,
            energy_level=energy_level,
            anxiety_level=anxiety_level,
            primary_emotion=primary_emotion,
            secondary_emotions=secondary_emotions,
            emotional_need=emotional_need,
            note=note,
            source=source,
            requires_attention=requires_attention,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def get_recent(
        self, user_id: int, days: int = 7
    ) -> list[MoodEntry]:
        """Get recent mood entries."""
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(MoodEntry)
            .where(
                and_(
                    MoodEntry.user_id == user_id,
                    MoodEntry.created_at >= since,
                )
            )
            .order_by(MoodEntry.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_average_mood(self, user_id: int, days: int = 7) -> Optional[float]:
        """Get average mood score for last N days."""
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(func.avg(MoodEntry.mood_score))
            .where(
                and_(
                    MoodEntry.user_id == user_id,
                    MoodEntry.created_at >= since,
                )
            )
        )
        return result.scalar()


class PaymentRepository:
    """Repository for Payment operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        amount: float,
        plan: str,
        provider: str = "yookassa",
        external_id: Optional[str] = None,
    ) -> Payment:
        """Create payment record."""
        payment = Payment(
            user_id=user_id,
            amount=amount,
            plan=plan,
            provider=provider,
            external_id=external_id,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def get_by_external_id(self, external_id: str) -> Optional[Payment]:
        """Get payment by external ID."""
        result = await self.session.execute(
            select(Payment).where(Payment.external_id == external_id)
        )
        return result.scalar_one_or_none()

    async def mark_succeeded(self, payment_id: int) -> None:
        """Mark payment as succeeded."""
        await self.session.execute(
            update(Payment)
            .where(Payment.id == payment_id)
            .values(
                status=PaymentStatus.SUCCEEDED.value,
                completed_at=func.now(),
            )
        )

    async def mark_failed(self, payment_id: int) -> None:
        """Mark payment as failed."""
        await self.session.execute(
            update(Payment)
            .where(Payment.id == payment_id)
            .values(status=PaymentStatus.FAILED.value)
        )


class UsageLogRepository:
    """Repository for UsageLog operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def increment(
        self,
        user_id: int,
        messages: int = 1,
        tokens: int = 0,
        cost_cents: int = 0,
    ) -> UsageLog:
        """Increment usage counters for today."""
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        # Try to get existing record
        result = await self.session.execute(
            select(UsageLog).where(
                and_(UsageLog.user_id == user_id, UsageLog.date == today)
            )
        )
        log = result.scalar_one_or_none()

        if log:
            log.messages_count += messages
            log.tokens_used += tokens
            log.cost_cents += cost_cents
        else:
            log = UsageLog(
                user_id=user_id,
                date=today,
                messages_count=messages,
                tokens_used=tokens,
                cost_cents=cost_cents,
            )
            self.session.add(log)

        await self.session.flush()
        return log

    async def get_today(self, user_id: int) -> Optional[UsageLog]:
        """Get today's usage log."""
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self.session.execute(
            select(UsageLog).where(
                and_(UsageLog.user_id == user_id, UsageLog.date == today)
            )
        )
        return result.scalar_one_or_none()


class ConversationSummaryRepository:
    """Repository for ConversationSummary operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        summary: str,
        from_message_id: int,
        to_message_id: int,
        messages_count: int,
    ) -> ConversationSummary:
        """Create conversation summary."""
        entry = ConversationSummary(
            user_id=user_id,
            summary=summary,
            from_message_id=from_message_id,
            to_message_id=to_message_id,
            messages_count=messages_count,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def get_recent(self, user_id: int, limit: int = 5) -> list[ConversationSummary]:
        """Get recent summaries."""
        result = await self.session.execute(
            select(ConversationSummary)
            .where(ConversationSummary.user_id == user_id)
            .order_by(ConversationSummary.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
