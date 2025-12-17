from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class SubscriptionPlan(str, Enum):
    """Subscription plan types."""
    FREE = "free"
    BASIC = "basic"
    PREMIUM = "premium"


class SubscriptionStatus(str, Enum):
    """Subscription status."""
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PENDING = "pending"


class PaymentStatus(str, Enum):
    """Payment status."""
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


class User(Base):
    """User model."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)

    # Profile
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Moscow")
    language: Mapped[str] = mapped_column(String(10), default="ru")

    # Onboarding state
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarding_step: Mapped[int] = mapped_column(Integer, default=0)

    # Preferences (stored as JSON)
    preferences: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Structured profile for personalization
    profile: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    last_active_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    messages: Mapped[list["Message"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    memories: Mapped[list["Memory"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    subscription: Mapped[Optional["Subscription"]] = relationship(back_populates="user", uselist=False)
    payments: Mapped[list["Payment"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    usage_logs: Mapped[list["UsageLog"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    mood_entries: Mapped[list["MoodEntry"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_users_created_at", "created_at"),
    )


class Subscription(Base):
    """User subscription model."""
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    plan: Mapped[str] = mapped_column(String(20), default=SubscriptionPlan.FREE.value)
    status: Mapped[str] = mapped_column(String(20), default=SubscriptionStatus.ACTIVE.value)

    # Dates
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Auto-renewal
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship
    user: Mapped["User"] = relationship(back_populates="subscription")

    __table_args__ = (
        Index("ix_subscriptions_expires_at", "expires_at"),
        Index("ix_subscriptions_status", "status"),
    )


class Message(Base):
    """Chat message model."""
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Metadata
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # For context management
    is_summarized: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationship
    user: Mapped["User"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_messages_user_created", "user_id", "created_at"),
    )


class Memory(Base):
    """Long-term memory about user."""
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Memory content
    fact: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="general")
    # Categories: identity, relationships, struggles, strengths, triggers, coping, values, history
    importance: Mapped[int] = mapped_column(Integer, default=5)  # 1-10 scale

    # Emotional weight - how to handle this topic
    emotional_weight: Mapped[str] = mapped_column(String(20), default="neutral")
    # neutral, positive, painful - painful topics need careful handling

    # Source tracking
    source_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # For semantic search (future)
    embedding: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationship
    user: Mapped["User"] = relationship(back_populates="memories")

    __table_args__ = (
        Index("ix_memories_user_category", "user_id", "category"),
        Index("ix_memories_user_importance", "user_id", "importance"),
    )


class MoodEntry(Base):
    """Mood tracking entries."""
    __tablename__ = "mood_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Mood data (1-10 scale)
    mood_score: Mapped[int] = mapped_column(Integer, nullable=False)
    energy_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    anxiety_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Detected emotions
    primary_emotion: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # grustь, zlost', strax, radost', styd, vina, odinochestvo, pustota, trevoga, ustalost', razdrazhenie, nadezhda
    secondary_emotions: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # What the person needs
    emotional_need: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # byt'_uslyshannym, podderzhka, sovet, otvlech'sya, vygovorit'sya, ne_byt'_odnomu

    # Optional note
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # How was it detected
    source: Mapped[str] = mapped_column(String(20), default="auto")  # "auto" or "manual"

    # Crisis flag
    requires_attention: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationship
    user: Mapped["User"] = relationship(back_populates="mood_entries")

    __table_args__ = (
        Index("ix_mood_entries_user_created", "user_id", "created_at"),
    )


class Payment(Base):
    """Payment records."""
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Payment details
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.PENDING.value)

    # Provider info
    provider: Mapped[str] = mapped_column(String(50), default="yookassa")
    external_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)

    # What was purchased
    plan: Mapped[str] = mapped_column(String(20), nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationship
    user: Mapped["User"] = relationship(back_populates="payments")

    __table_args__ = (
        Index("ix_payments_user_created", "user_id", "created_at"),
        Index("ix_payments_external_id", "external_id"),
    )


class UsageLog(Base):
    """Daily usage tracking."""
    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Date (for grouping)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Counters
    messages_count: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)

    # Cost tracking (in USD cents)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship
    user: Mapped["User"] = relationship(back_populates="usage_logs")

    __table_args__ = (
        Index("ix_usage_logs_user_date", "user_id", "date", unique=True),
    )


class ConversationSummary(Base):
    """Summaries of long conversations for context management."""
    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Summary content
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # Range of messages summarized
    from_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    to_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    messages_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_conversation_summaries_user", "user_id", "created_at"),
    )
