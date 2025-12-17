"""
Memory management system for user context.

This is what makes the bot feel like it "remembers" the user -
their struggles, their strengths, what helps them, what hurts.
"""

from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.claude import get_claude_client
from app.db.repository import (
    ConversationSummaryRepository,
    MemoryRepository,
    MessageRepository,
    MoodRepository,
)

logger = structlog.get_logger()


class MemoryManager:
    """
    Manages user memory - extraction, storage, and retrieval.

    The goal is to make every conversation feel like a continuation,
    not a new start. The bot should remember:
    - Who the person is (name, age, situation)
    - What they struggle with
    - What helps them cope
    - What topics are painful
    - Their communication patterns
    """

    # How many messages before we try to extract memories
    EXTRACTION_THRESHOLD = 10

    # How many messages before we create a summary
    SUMMARY_THRESHOLD = 50

    def __init__(self, session: AsyncSession):
        self.session = session
        self.memory_repo = MemoryRepository(session)
        self.message_repo = MessageRepository(session)
        self.mood_repo = MoodRepository(session)
        self.summary_repo = ConversationSummaryRepository(session)
        self.claude = get_claude_client()

    async def process_message(
        self,
        user_id: int,
        message: str,
        role: str = "user",
    ) -> dict:
        """
        Process a message - detect mood, extract memories if needed.

        Returns dict with:
        - mood_detected: Optional mood data
        - memories_extracted: Number of new memories
        - requires_attention: Whether message needs crisis response
        - emotional_need: What the person needs right now
        - primary_emotion: The main emotion detected
        """
        result = {
            "mood_detected": None,
            "memories_extracted": 0,
            "requires_attention": False,
            "attention_reason": None,
            "emotional_need": None,
            "primary_emotion": None,
        }

        # Only process user messages for mood/memory
        if role != "user":
            return result

        # Get recent context
        recent_messages = await self.message_repo.get_recent(user_id, limit=10)
        context = [(m.role, m.content) for m in recent_messages]

        # Detect mood
        mood_data = await self.claude.detect_mood(message, context)
        if mood_data:
            result["mood_detected"] = mood_data
            result["primary_emotion"] = mood_data.get("primary_emotion")
            result["emotional_need"] = mood_data.get("emotional_need")

            # Save mood entry with all the rich data
            await self.mood_repo.add(
                user_id=user_id,
                mood_score=mood_data.get("mood_score", 5),
                energy_level=mood_data.get("energy_level"),
                anxiety_level=mood_data.get("anxiety_level"),
                primary_emotion=mood_data.get("primary_emotion"),
                secondary_emotions=mood_data.get("secondary_emotions"),
                emotional_need=mood_data.get("emotional_need"),
                source="auto",
                requires_attention=mood_data.get("requires_attention", False),
            )

            # Check if requires attention
            if mood_data.get("requires_attention"):
                result["requires_attention"] = True
                result["attention_reason"] = mood_data.get("crisis_indicators", [])
                logger.warning(
                    "Message requires attention",
                    user_id=user_id,
                    indicators=mood_data.get("crisis_indicators"),
                )

        # Check if we should extract memories
        messages_count = len(recent_messages)
        if messages_count >= self.EXTRACTION_THRESHOLD:
            extracted = await self._extract_and_save_memories(user_id, context)
            result["memories_extracted"] = extracted

        return result

    async def _extract_and_save_memories(
        self,
        user_id: int,
        conversation: list[tuple[str, str]],
    ) -> int:
        """Extract and save new memories from conversation."""
        # Get existing facts to avoid duplicates
        existing_memories = await self.memory_repo.get_all(user_id)
        known_facts = [m.fact for m in existing_memories]

        # Extract new facts
        new_facts = await self.claude.extract_memories(conversation, known_facts)

        # Save new memories
        saved_count = 0
        for fact_data in new_facts:
            try:
                await self.memory_repo.add(
                    user_id=user_id,
                    fact=fact_data["fact"],
                    category=fact_data.get("category", "general"),
                    importance=fact_data.get("importance", 5),
                    emotional_weight=fact_data.get("emotional_weight", "neutral"),
                )
                saved_count += 1
            except Exception as e:
                logger.error("Failed to save memory", error=str(e))

        if saved_count > 0:
            logger.info(
                "Extracted memories",
                user_id=user_id,
                count=saved_count,
            )

        return saved_count

    async def should_summarize(self, user_id: int) -> bool:
        """Check if conversation should be summarized."""
        # Get last summary
        summaries = await self.summary_repo.get_recent(user_id, limit=1)
        if not summaries:
            # No summaries yet - check total message count
            messages = await self.message_repo.get_recent(user_id, limit=self.SUMMARY_THRESHOLD + 1)
            return len(messages) >= self.SUMMARY_THRESHOLD

        last_summary = summaries[0]

        # Count messages since last summary
        messages = await self.message_repo.get_recent(user_id, limit=100)
        messages_since_summary = [
            m for m in messages if m.id > last_summary.to_message_id
        ]

        return len(messages_since_summary) >= self.SUMMARY_THRESHOLD

    async def create_summary(self, user_id: int) -> Optional[str]:
        """Create and save conversation summary."""
        # Get messages to summarize
        messages = await self.message_repo.get_recent(user_id, limit=self.SUMMARY_THRESHOLD)
        if len(messages) < 10:  # Need at least some messages
            return None

        conversation = [(m.role, m.content) for m in messages]

        # Create summary
        summary = await self.claude.summarize_conversation(conversation)
        if not summary:
            return None

        # Save summary
        await self.summary_repo.create(
            user_id=user_id,
            summary=summary,
            from_message_id=messages[0].id,
            to_message_id=messages[-1].id,
            messages_count=len(messages),
        )

        # Mark messages as summarized
        message_ids = [m.id for m in messages]
        await self.message_repo.mark_as_summarized(message_ids)

        logger.info(
            "Created conversation summary",
            user_id=user_id,
            messages_count=len(messages),
        )

        return summary

    async def get_context_for_response(
        self,
        user_id: int,
        user_data: dict,
    ) -> dict:
        """
        Get all context needed for generating a response.

        Returns dict with:
        - messages: Recent messages
        - memories: User memories as dicts (with emotional_weight)
        - mood_history: Recent mood entries as dicts
        - summaries: Conversation summaries
        - time_of_day: Current time period
        - days_since_last_chat: Days since last activity
        - current_mood: Most recent mood entry
        """
        # Get recent messages
        messages = await self.message_repo.get_recent(user_id, limit=20)

        # Get memories - include emotional_weight
        memories = await self.memory_repo.get_all(user_id)

        # Get mood history
        mood_entries = await self.mood_repo.get_recent(user_id, days=7)

        # Get summaries
        summaries = await self.summary_repo.get_recent(user_id, limit=3)

        # Calculate time of day
        hour = datetime.now().hour
        if 5 <= hour < 12:
            time_of_day = "morning"
        elif 12 <= hour < 17:
            time_of_day = "afternoon"
        elif 17 <= hour < 22:
            time_of_day = "evening"
        else:
            time_of_day = "night"

        # Calculate days since last chat
        days_since_last_chat = 0
        if user_data.get("last_active_at"):
            last_active = user_data["last_active_at"]
            if isinstance(last_active, str):
                last_active = datetime.fromisoformat(last_active)
            delta = datetime.utcnow() - last_active
            days_since_last_chat = delta.days

        # Get current mood (most recent)
        current_mood = None
        if mood_entries:
            latest = mood_entries[0]
            current_mood = {
                "mood_score": latest.mood_score,
                "primary_emotion": latest.primary_emotion,
                "emotional_need": latest.emotional_need,
                "anxiety_level": latest.anxiety_level,
            }

        return {
            "messages": [(m.role, m.content) for m in messages],
            "memories": [
                {
                    "fact": m.fact,
                    "category": m.category,
                    "importance": m.importance,
                    "emotional_weight": m.emotional_weight,
                }
                for m in memories
            ],
            "mood_history": [
                {
                    "mood_score": m.mood_score,
                    "primary_emotion": m.primary_emotion,
                    "created_at": m.created_at,
                }
                for m in mood_entries
            ],
            "summaries": [s.summary for s in summaries],
            "time_of_day": time_of_day,
            "days_since_last_chat": days_since_last_chat,
            "current_mood": current_mood,
        }

    async def get_last_mood(self, user_id: int) -> Optional[dict]:
        """Get the most recent mood entry for a user."""
        entries = await self.mood_repo.get_recent(user_id, days=30)
        if not entries:
            return None

        latest = entries[0]
        return {
            "mood_score": latest.mood_score,
            "primary_emotion": latest.primary_emotion,
            "emotional_need": latest.emotional_need,
            "anxiety_level": latest.anxiety_level,
            "requires_attention": latest.requires_attention,
            "created_at": latest.created_at,
        }

    async def get_painful_topics(self, user_id: int) -> list[str]:
        """Get list of painful topics that need careful handling."""
        memories = await self.memory_repo.get_all(user_id)
        return [
            m.fact for m in memories
            if m.emotional_weight == "painful"
        ]
