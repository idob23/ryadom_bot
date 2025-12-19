from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConversationSummary,
    LifeEvent,
    Memory,
    Message,
    MoodEntry,
    Payment,
    PaymentStatus,
    Person,
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
        """Update user's preferences (merges with existing)."""
        # Get current preferences
        result = await self.session.execute(
            select(User.preferences).where(User.id == user_id)
        )
        current = result.scalar_one_or_none() or {}

        # Merge new preferences
        merged = {**current, **preferences}

        await self.session.execute(
            update(User).where(User.id == user_id).values(preferences=merged)
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
    """Repository for Memory operations - the eternal friend's memory."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        user_id: int,
        fact: str,
        category: str = "general",
        importance: int = 5,
        emotional_weight: str = "neutral",
        tags: Optional[list] = None,
        memory_key: Optional[str] = None,
        source_message_id: Optional[int] = None,
    ) -> Memory:
        """Add a memory fact."""
        memory = Memory(
            user_id=user_id,
            fact=fact,
            category=category,
            importance=importance,
            emotional_weight=emotional_weight,
            tags=tags,
            memory_key=memory_key,
            source_message_id=source_message_id,
            is_current=True,
        )
        self.session.add(memory)
        await self.session.flush()
        return memory

    async def get_by_key(self, user_id: int, memory_key: str) -> Optional[Memory]:
        """Get memory by unique key for updates."""
        result = await self.session.execute(
            select(Memory)
            .where(
                and_(
                    Memory.user_id == user_id,
                    Memory.memory_key == memory_key,
                    Memory.is_current == True,
                )
            )
        )
        return result.scalar_one_or_none()

    async def update_memory(
        self,
        memory_id: int,
        new_fact: str,
        old_fact: str,
    ) -> Memory:
        """Update a memory, keeping history."""
        result = await self.session.execute(
            select(Memory).where(Memory.id == memory_id)
        )
        memory = result.scalar_one()

        # Add to history
        history = memory.history or []
        history.append({
            "old_value": old_fact,
            "changed_at": datetime.utcnow().isoformat(),
        })

        memory.fact = new_fact
        memory.history = history
        memory.updated_at = datetime.utcnow()

        await self.session.flush()
        return memory

    async def get_all(self, user_id: int, current_only: bool = True) -> list[Memory]:
        """Get all memories for a user."""
        query = select(Memory).where(Memory.user_id == user_id)
        if current_only:
            query = query.where(Memory.is_current == True)
        query = query.order_by(Memory.importance.desc(), Memory.created_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_category(self, user_id: int, category: str) -> list[Memory]:
        """Get memories by category."""
        result = await self.session.execute(
            select(Memory)
            .where(
                and_(
                    Memory.user_id == user_id,
                    Memory.category == category,
                    Memory.is_current == True,
                )
            )
            .order_by(Memory.importance.desc())
        )
        return list(result.scalars().all())

    async def get_important(self, user_id: int, min_importance: int = 7) -> list[Memory]:
        """Get important memories."""
        result = await self.session.execute(
            select(Memory)
            .where(
                and_(
                    Memory.user_id == user_id,
                    Memory.importance >= min_importance,
                    Memory.is_current == True,
                )
            )
            .order_by(Memory.importance.desc())
        )
        return list(result.scalars().all())

    async def search_by_tags(self, user_id: int, search_tags: list[str]) -> list[Memory]:
        """Search memories by tags (any match)."""
        # For SQLite/PostgreSQL JSON array search
        all_memories = await self.get_all(user_id)
        matching = []
        search_tags_lower = [t.lower() for t in search_tags]

        for memory in all_memories:
            if memory.tags:
                memory_tags_lower = [t.lower() for t in memory.tags]
                if any(tag in memory_tags_lower for tag in search_tags_lower):
                    matching.append(memory)

        return matching

    async def search_by_text(self, user_id: int, search_text: str) -> list[Memory]:
        """Search memories by text content."""
        all_memories = await self.get_all(user_id)
        search_lower = search_text.lower()

        matching = [
            m for m in all_memories
            if search_lower in m.fact.lower()
        ]
        return matching

    async def mark_accessed(self, memory_ids: list[int]) -> None:
        """Mark memories as accessed (for relevance tracking)."""
        await self.session.execute(
            update(Memory)
            .where(Memory.id.in_(memory_ids))
            .values(last_accessed_at=func.now())
        )

    async def update_importance(self, memory_id: int, importance: int) -> None:
        """Update memory importance."""
        await self.session.execute(
            update(Memory)
            .where(Memory.id == memory_id)
            .values(importance=importance, last_accessed_at=func.now())
        )


class PersonRepository:
    """Repository for Person operations - people in user's life."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        user_id: int,
        name: str,
        relation: str,
        notes: Optional[str] = None,
        emotional_tone: str = "neutral",
        important_dates: Optional[dict] = None,
    ) -> Person:
        """Add a person to user's circle."""
        person = Person(
            user_id=user_id,
            name=name,
            relation=relation,
            notes=notes,
            emotional_tone=emotional_tone,
            important_dates=important_dates,
            is_active=True,
        )
        self.session.add(person)
        await self.session.flush()
        return person

    async def get_all(self, user_id: int, active_only: bool = True) -> list[Person]:
        """Get all persons for a user."""
        query = select(Person).where(Person.user_id == user_id)
        if active_only:
            query = query.where(Person.is_active == True)
        query = query.order_by(Person.created_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_name(self, user_id: int, name: str) -> Optional[Person]:
        """Get person by name (case-insensitive partial match)."""
        all_persons = await self.get_all(user_id)
        name_lower = name.lower()

        for person in all_persons:
            if name_lower in person.name.lower():
                return person
        return None

    async def get_by_relation(self, user_id: int, relation: str) -> list[Person]:
        """Get persons by relation type."""
        result = await self.session.execute(
            select(Person)
            .where(
                and_(
                    Person.user_id == user_id,
                    Person.relation == relation,
                    Person.is_active == True,
                )
            )
        )
        return list(result.scalars().all())

    async def update(
        self,
        person_id: int,
        notes: Optional[str] = None,
        emotional_tone: Optional[str] = None,
        important_dates: Optional[dict] = None,
    ) -> None:
        """Update person information."""
        values = {"updated_at": func.now()}
        if notes is not None:
            values["notes"] = notes
        if emotional_tone is not None:
            values["emotional_tone"] = emotional_tone
        if important_dates is not None:
            values["important_dates"] = important_dates

        await self.session.execute(
            update(Person)
            .where(Person.id == person_id)
            .values(**values)
        )

    async def get_upcoming_dates(self, user_id: int, days: int = 14) -> list[dict]:
        """Get upcoming important dates (birthdays, anniversaries)."""
        persons = await self.get_all(user_id)
        today = datetime.utcnow().date()
        upcoming = []

        for person in persons:
            if not person.important_dates:
                continue

            for date_type, date_str in person.important_dates.items():
                try:
                    date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    # Check if this year's occurrence is upcoming
                    this_year = date.replace(year=today.year)
                    if this_year < today:
                        this_year = date.replace(year=today.year + 1)

                    days_until = (this_year - today).days
                    if 0 <= days_until <= days:
                        upcoming.append({
                            "person_name": person.name,
                            "date_type": date_type,
                            "date": this_year.isoformat(),
                            "days_until": days_until,
                        })
                except (ValueError, TypeError):
                    continue

        return sorted(upcoming, key=lambda x: x["days_until"])


class LifeEventRepository:
    """Repository for LifeEvent operations - significant events."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(
        self,
        user_id: int,
        title: str,
        description: Optional[str] = None,
        event_date: Optional[datetime] = None,
        is_recurring: bool = False,
        recurrence_type: Optional[str] = None,
        emotional_weight: str = "neutral",
        related_person_id: Optional[int] = None,
        tags: Optional[list] = None,
    ) -> LifeEvent:
        """Add a life event."""
        event = LifeEvent(
            user_id=user_id,
            title=title,
            description=description,
            event_date=event_date,
            is_recurring=is_recurring,
            recurrence_type=recurrence_type,
            emotional_weight=emotional_weight,
            related_person_id=related_person_id,
            tags=tags,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_all(self, user_id: int) -> list[LifeEvent]:
        """Get all events for a user."""
        result = await self.session.execute(
            select(LifeEvent)
            .where(LifeEvent.user_id == user_id)
            .order_by(LifeEvent.event_date.desc().nullslast())
        )
        return list(result.scalars().all())

    async def get_recent(self, user_id: int, days: int = 30) -> list[LifeEvent]:
        """Get recent events."""
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(LifeEvent)
            .where(
                and_(
                    LifeEvent.user_id == user_id,
                    LifeEvent.created_at >= since,
                )
            )
            .order_by(LifeEvent.created_at.desc())
        )
        return list(result.scalars().all())

    async def search_by_tags(self, user_id: int, search_tags: list[str]) -> list[LifeEvent]:
        """Search events by tags."""
        all_events = await self.get_all(user_id)
        search_tags_lower = [t.lower() for t in search_tags]

        matching = []
        for event in all_events:
            if event.tags:
                event_tags_lower = [t.lower() for t in event.tags]
                if any(tag in event_tags_lower for tag in search_tags_lower):
                    matching.append(event)

        return matching

    async def get_by_person(self, user_id: int, person_id: int) -> list[LifeEvent]:
        """Get events related to a specific person."""
        result = await self.session.execute(
            select(LifeEvent)
            .where(
                and_(
                    LifeEvent.user_id == user_id,
                    LifeEvent.related_person_id == person_id,
                )
            )
            .order_by(LifeEvent.event_date.desc().nullslast())
        )
        return list(result.scalars().all())


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
