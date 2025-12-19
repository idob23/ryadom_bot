"""
Memory management system for user context.

This is the heart of the bot - what makes it feel like a true friend
who remembers EVERYTHING about the user, forever.
"""

import re
from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.claude import get_claude_client
from app.db.repository import (
    ConversationSummaryRepository,
    LifeEventRepository,
    MemoryRepository,
    MessageRepository,
    MoodRepository,
    PersonRepository,
    UserRepository,
)

logger = structlog.get_logger()


class MemoryManager:
    """
    Manages user memory - extraction, storage, and retrieval.

    The goal is to make the bot remember like a real friend:
    - Every person mentioned (family, friends, colleagues)
    - Every significant event (good and bad)
    - All facts about the person
    - How things change over time (job, relationships, mood)

    Memory is extracted after EVERY message, not periodically.
    """

    # How many messages before we create a summary
    SUMMARY_THRESHOLD = 50

    def __init__(self, session: AsyncSession):
        self.session = session
        self.memory_repo = MemoryRepository(session)
        self.person_repo = PersonRepository(session)
        self.event_repo = LifeEventRepository(session)
        self.message_repo = MessageRepository(session)
        self.mood_repo = MoodRepository(session)
        self.summary_repo = ConversationSummaryRepository(session)
        self.user_repo = UserRepository(session)
        self.claude = get_claude_client()

    async def process_message(
        self,
        user_id: int,
        message: str,
        role: str = "user",
    ) -> dict:
        """
        Process a message - detect mood, extract ALL memories.

        This runs after EVERY user message to ensure nothing is forgotten.

        Returns dict with:
        - mood_detected: Optional mood data
        - memories_extracted: Number of new memories
        - persons_found: Number of new/updated persons
        - events_found: Number of new events
        - requires_attention: Whether message needs crisis response
        - emotional_need: What the person needs right now
        - primary_emotion: The main emotion detected
        """
        result = {
            "mood_detected": None,
            "memories_extracted": 0,
            "persons_found": 0,
            "events_found": 0,
            "updates_applied": 0,
            "requires_attention": False,
            "attention_reason": None,
            "emotional_need": None,
            "primary_emotion": None,
        }

        # Only process user messages for mood/memory
        if role != "user":
            return result

        # Get recent context for better understanding
        recent_messages = await self.message_repo.get_recent(user_id, limit=10)
        context = [(m.role, m.content) for m in recent_messages]

        # Detect mood (always)
        mood_data = await self.claude.detect_mood(message, context)
        if mood_data:
            result["mood_detected"] = mood_data
            result["primary_emotion"] = mood_data.get("primary_emotion")
            result["emotional_need"] = mood_data.get("emotional_need")

            # Save mood entry
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

        # Extract memories from EVERY message
        extracted = await self._extract_all_memories(user_id, message, context)
        result["memories_extracted"] = extracted.get("facts", 0)
        result["persons_found"] = extracted.get("persons", 0)
        result["events_found"] = extracted.get("events", 0)
        result["updates_applied"] = extracted.get("updates", 0)

        return result

    async def _extract_all_memories(
        self,
        user_id: int,
        message: str,
        context: list[tuple[str, str]],
    ) -> dict:
        """Extract facts, persons, events from the message."""
        # Get existing data for context
        existing_memories = await self.memory_repo.get_all(user_id)
        existing_persons = await self.person_repo.get_all(user_id)

        known_facts = [m.fact for m in existing_memories]
        known_persons = [
            f"{p.name} ({p.relation})" for p in existing_persons
        ]

        # Format context
        context_str = "\n".join([
            f"{'Человек' if role == 'user' else 'Бот'}: {content}"
            for role, content in context[-5:]  # Last 5 messages
        ])

        # Call Claude to extract
        extraction = await self.claude.extract_full_memory(
            message=message,
            conversation=context_str,
            known_facts=known_facts,
            known_persons=known_persons,
        )

        if not extraction:
            return {"facts": 0, "persons": 0, "events": 0, "updates": 0}

        counts = {"facts": 0, "persons": 0, "events": 0, "updates": 0}

        # Process facts
        for fact_data in extraction.get("facts", []):
            try:
                # Special handling for user's name
                memory_key = fact_data.get("memory_key")
                if memory_key == "user_name":
                    # Extract name from fact and update user
                    name = self._extract_name_from_fact(fact_data["fact"])
                    if name:
                        await self.user_repo.update_name(user_id, name)
                        logger.info("Updated user name from conversation", user_id=user_id, name=name)

                await self.memory_repo.add(
                    user_id=user_id,
                    fact=fact_data["fact"],
                    category=fact_data.get("category", "general"),
                    importance=fact_data.get("importance", 5),
                    emotional_weight=fact_data.get("emotional_weight", "neutral"),
                    tags=fact_data.get("tags"),
                    memory_key=memory_key,
                )
                counts["facts"] += 1
            except Exception as e:
                logger.error("Failed to save memory", error=str(e))

        # Process persons
        for person_data in extraction.get("persons", []):
            try:
                # Check if person already exists
                existing = await self.person_repo.get_by_name(
                    user_id, person_data["name"]
                )
                if existing:
                    # Update existing person
                    await self.person_repo.update(
                        person_id=existing.id,
                        notes=person_data.get("notes"),
                        emotional_tone=person_data.get("emotional_tone", "neutral"),
                    )
                else:
                    # Add new person
                    await self.person_repo.add(
                        user_id=user_id,
                        name=person_data["name"],
                        relation=person_data.get("relation", "знакомый"),
                        notes=person_data.get("notes"),
                        emotional_tone=person_data.get("emotional_tone", "neutral"),
                    )
                counts["persons"] += 1
            except Exception as e:
                logger.error("Failed to save person", error=str(e))

        # Process events
        for event_data in extraction.get("events", []):
            try:
                # Parse date if provided
                event_date = None
                if event_data.get("event_date"):
                    try:
                        event_date = datetime.strptime(
                            event_data["event_date"], "%Y-%m-%d"
                        )
                    except ValueError:
                        pass

                # Find related person if mentioned
                related_person_id = None
                if event_data.get("related_person"):
                    person = await self.person_repo.get_by_name(
                        user_id, event_data["related_person"]
                    )
                    if person:
                        related_person_id = person.id

                await self.event_repo.add(
                    user_id=user_id,
                    title=event_data["title"],
                    description=event_data.get("description"),
                    event_date=event_date,
                    is_recurring=event_data.get("is_recurring", False),
                    emotional_weight=event_data.get("emotional_weight", "neutral"),
                    related_person_id=related_person_id,
                    tags=event_data.get("tags"),
                )
                counts["events"] += 1
            except Exception as e:
                logger.error("Failed to save event", error=str(e))

        # Process updates (memory corrections)
        for update_data in extraction.get("updates", []):
            try:
                # Find memory to update by key or text search
                memory = None
                if update_data.get("memory_key"):
                    memory = await self.memory_repo.get_by_key(
                        user_id, update_data["memory_key"]
                    )

                if not memory and update_data.get("old_fact_contains"):
                    # Search by text
                    matches = await self.memory_repo.search_by_text(
                        user_id, update_data["old_fact_contains"]
                    )
                    if matches:
                        memory = matches[0]

                if memory:
                    await self.memory_repo.update_memory(
                        memory_id=memory.id,
                        new_fact=update_data["new_fact"],
                        old_fact=memory.fact,
                    )
                    counts["updates"] += 1
                    logger.info(
                        "Memory updated",
                        user_id=user_id,
                        old=memory.fact[:50],
                        new=update_data["new_fact"][:50],
                    )
            except Exception as e:
                logger.error("Failed to update memory", error=str(e))

        if any(counts.values()):
            logger.info(
                "Extracted memories",
                user_id=user_id,
                **counts,
            )

        return counts

    async def get_relevant_context(
        self,
        user_id: int,
        current_message: str,
        user_data: dict,
    ) -> dict:
        """
        Get ALL relevant context for generating a response.

        This is the key function - it gathers everything the bot
        should "remember" for this specific conversation.
        """
        # Extract keywords from current message for relevance search
        keywords = self._extract_keywords(current_message)

        # Get ALL memories (they're already sorted by importance)
        all_memories = await self.memory_repo.get_all(user_id)

        # Get relevant memories based on keywords
        relevant_by_tags = []
        if keywords:
            relevant_by_tags = await self.memory_repo.search_by_tags(user_id, keywords)
            relevant_by_text = await self.memory_repo.search_by_text(
                user_id, current_message
            )
            # Mark as accessed
            accessed_ids = [m.id for m in relevant_by_tags + relevant_by_text]
            if accessed_ids:
                await self.memory_repo.mark_accessed(accessed_ids)

        # Get all persons
        persons = await self.person_repo.get_all(user_id)

        # Get recent events
        recent_events = await self.event_repo.get_recent(user_id, days=30)

        # Get upcoming important dates
        upcoming_dates = await self.person_repo.get_upcoming_dates(user_id, days=14)

        # Get recent messages
        messages = await self.message_repo.get_recent(user_id, limit=20)

        # Get mood history
        mood_entries = await self.mood_repo.get_recent(user_id, days=7)

        # Get summaries
        summaries = await self.summary_repo.get_recent(user_id, limit=3)

        # Calculate time context
        hour = datetime.now().hour
        if 5 <= hour < 12:
            time_of_day = "morning"
        elif 12 <= hour < 17:
            time_of_day = "afternoon"
        elif 17 <= hour < 22:
            time_of_day = "evening"
        else:
            time_of_day = "night"

        # Days since last chat
        days_since_last_chat = 0
        if user_data.get("last_active_at"):
            last_active = user_data["last_active_at"]
            if isinstance(last_active, str):
                last_active = datetime.fromisoformat(last_active)
            delta = datetime.utcnow() - last_active
            days_since_last_chat = delta.days

        # Current mood
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
            "all_memories": [
                {
                    "fact": m.fact,
                    "category": m.category,
                    "importance": m.importance,
                    "emotional_weight": m.emotional_weight,
                    "tags": m.tags,
                }
                for m in all_memories
            ],
            "relevant_memories": [
                {
                    "fact": m.fact,
                    "category": m.category,
                    "importance": m.importance,
                }
                for m in relevant_by_tags[:10]  # Top 10 relevant
            ],
            "persons": [
                {
                    "name": p.name,
                    "relation": p.relation,
                    "notes": p.notes,
                    "emotional_tone": p.emotional_tone,
                }
                for p in persons
            ],
            "recent_events": [
                {
                    "title": e.title,
                    "description": e.description,
                    "event_date": e.event_date.isoformat() if e.event_date else None,
                    "emotional_weight": e.emotional_weight,
                }
                for e in recent_events[:10]
            ],
            "upcoming_dates": upcoming_dates,
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

    def _extract_name_from_fact(self, fact: str) -> str | None:
        """Extract actual name from a fact like 'Имя: Игорь' or 'Зовут Игорь'."""
        # Common patterns
        patterns = [
            r'(?:имя|зовут|называть)\s*[:\-]?\s*([А-ЯЁA-Z][а-яёa-z]+)',
            r'([А-ЯЁA-Z][а-яёa-z]+)',  # Just a capitalized word as fallback
        ]
        for pattern in patterns:
            match = re.search(pattern, fact, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                # Skip generic words
                if name.lower() not in {'друг', 'человек', 'пользователь', 'имя'}:
                    return name.capitalize()
        return None

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract keywords from text for memory search."""
        # Simple keyword extraction - remove common words
        stop_words = {
            "я", "ты", "он", "она", "мы", "вы", "они", "это", "что", "как",
            "так", "но", "и", "или", "не", "да", "нет", "мне", "меня", "тебе",
            "его", "её", "их", "нас", "вас", "быть", "был", "была", "было",
            "будет", "есть", "для", "на", "по", "от", "до", "при", "после",
            "уже", "ещё", "тоже", "очень", "просто", "вот", "там", "тут",
            "сегодня", "вчера", "завтра", "когда", "если", "чтобы", "потому",
        }

        # Extract words
        words = re.findall(r'[а-яёa-z]+', text.lower())

        # Filter
        keywords = [
            w for w in words
            if len(w) > 2 and w not in stop_words
        ]

        return keywords[:10]  # Max 10 keywords

    async def should_summarize(self, user_id: int) -> bool:
        """Check if conversation should be summarized."""
        summaries = await self.summary_repo.get_recent(user_id, limit=1)
        if not summaries:
            messages = await self.message_repo.get_recent(
                user_id, limit=self.SUMMARY_THRESHOLD + 1
            )
            return len(messages) >= self.SUMMARY_THRESHOLD

        last_summary = summaries[0]
        messages = await self.message_repo.get_recent(user_id, limit=100)
        messages_since_summary = [
            m for m in messages if m.id > last_summary.to_message_id
        ]

        return len(messages_since_summary) >= self.SUMMARY_THRESHOLD

    async def create_summary(self, user_id: int) -> Optional[str]:
        """Create and save conversation summary."""
        messages = await self.message_repo.get_recent(
            user_id, limit=self.SUMMARY_THRESHOLD
        )
        if len(messages) < 10:
            return None

        conversation = [(m.role, m.content) for m in messages]
        summary = await self.claude.summarize_conversation(conversation)
        if not summary:
            return None

        await self.summary_repo.create(
            user_id=user_id,
            summary=summary,
            from_message_id=messages[0].id,
            to_message_id=messages[-1].id,
            messages_count=len(messages),
        )

        message_ids = [m.id for m in messages]
        await self.message_repo.mark_as_summarized(message_ids)

        logger.info(
            "Created conversation summary",
            user_id=user_id,
            messages_count=len(messages),
        )

        return summary

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

    async def get_persons_summary(self, user_id: int) -> str:
        """Get a summary of people in user's life for context."""
        persons = await self.person_repo.get_all(user_id)
        if not persons:
            return ""

        lines = []
        for p in persons[:15]:  # Max 15 persons in context
            line = f"- {p.name} ({p.relation})"
            if p.notes:
                line += f": {p.notes}"
            if p.emotional_tone != "neutral":
                line += f" [{p.emotional_tone}]"
            lines.append(line)

        return "\n".join(lines)
