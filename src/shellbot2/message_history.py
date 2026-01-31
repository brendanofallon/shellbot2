"""
Module for storing conversation history in a SQLite database using SQLAlchemy.

This module provides an append-only message history storage system where messages
can be added but not edited or removed.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import json
import logging
import re
import uuid

from rank_bm25 import BM25Okapi

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, desc
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    pass


class Message(Base):
    """
    SQLAlchemy model for storing conversation messages.
    
    Attributes:
        id: Auto-incrementing primary key
        thread_id: Identifier for the conversation thread
        interaction_id: Optional identifier grouping messages into an interaction
        message: JSON formatted string containing the message content
        created_at: Timestamp when the message was added
    """
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String, nullable=False, index=True)
    interaction_id = Column(String, nullable=True, index=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    def __repr__(self) -> str:
        return f"<Message(id={self.id}, thread_id={self.thread_id}, interaction_id={self.interaction_id}, created_at={self.created_at})>"


@dataclass
class Interaction:
    """
    Represents a group of messages that share the same interaction_id.
    
    Attributes:
        interaction_id: Unique identifier for this interaction
        thread_id: The thread this interaction belongs to
        messages: List of Message objects in this interaction
        created_at: Timestamp of the first message in the interaction
    """
    interaction_id: str
    thread_id: str
    messages: list[Message] = field(default_factory=list)
    created_at: Optional[str] = None


class MessageHistory:
    """
    Class for managing conversation history in a SQLite database.
    
    This class provides methods to add and retrieve messages from a SQLite database.
    Messages are append-only and cannot be edited or removed once added.
    
    Args:
        db_path: Path to the SQLite database file. If None, uses in-memory database.
    """
    
    def __init__(self, db_path: Optional[str | Path] = None):
        """
        Initialize the MessageHistory with a database connection.
        
        Args:
            db_path: Path to the SQLite database file. If None, creates an in-memory database.
        """
        if db_path is None:
            db_url = "sqlite:///:memory:"
        else:
            db_path = Path(db_path)
            db_url = f"sqlite:///{db_path}"
        logger.info(f"Creating engine with db_url: {db_url}")
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
    
    def add_message(self, thread_id: str, message: str) -> int:
        """
        Add a single message to the database.
        
        Args:
            thread_id: Identifier for the conversation thread
            message: JSON formatted string containing the message content
            
        Returns:
            The ID of the newly created message
        """
        with self.SessionLocal() as session:
            msg = Message(
                thread_id=thread_id,
                message=json.dumps(message),
                created_at=datetime.now()
            )
            session.add(msg)
            session.commit()
            session.refresh(msg)
            return msg.id
    
    def add_messages(self, thread_id: str, messages: list[str]) -> list[int]:
        """
        Add multiple messages to the database.
        
        All messages will have the same thread_id but each will have its own timestamp.
        
        Args:
            thread_id: Identifier for the conversation thread
            messages: List of JSON formatted strings containing message content
            
        Returns:
            List of IDs of the newly created messages
        """
        print(f"Adding messages: {messages}")
        with self.SessionLocal() as session:
            msg_objects = []
            for message in messages:
                msg = Message(
                    thread_id=thread_id,
                    message=json.dumps(message),
                    created_at=datetime.now()
                )
                session.add(msg)
                msg_objects.append(msg)
            
            session.commit()
            return [msg.id for msg in msg_objects]

    def add_interaction(self, thread_id: str, messages: list[str] | str) -> str:
        """
        Add one or more messages as a single interaction.
        
        All messages will share the same interaction_id, grouping them together
        as a logical unit.
        
        Args:
            thread_id: Identifier for the conversation thread
            messages: A single message string or list of message strings
            
        Returns:
            The generated interaction_id for the new interaction
        """
        if isinstance(messages, str):
            messages = [messages]
        
        interaction_id = str(uuid.uuid4())
        
        with self.SessionLocal() as session:
            for message in messages:
                msg = Message(
                    thread_id=thread_id,
                    interaction_id=interaction_id,
                    message=json.dumps(message),
                    created_at=datetime.now()
                )
                session.add(msg)
            
            session.commit()
            return interaction_id

    def get_all_interactions(self, thread_id: str) -> list[Interaction]:
        """
        Retrieve all interactions for a given thread.
        
        Groups messages by their interaction_id and returns them as Interaction objects.
        Messages without an interaction_id are each returned as their own single-message
        interaction with a generated ID.
        
        Args:
            thread_id: Identifier for the conversation thread
            
        Returns:
            List of Interaction objects, ordered by the creation time of their first message.
        """
        with self.SessionLocal() as session:
            messages = (
                session.query(Message)
                .filter(Message.thread_id == thread_id)
                .order_by(Message.created_at)
                .all()
            )
            
            # Group messages by interaction_id
            interactions_dict: dict[str, list[Message]] = {}
            standalone_messages: list[Message] = []
            
            for msg in messages:
                if msg.interaction_id:
                    if msg.interaction_id not in interactions_dict:
                        interactions_dict[msg.interaction_id] = []
                    interactions_dict[msg.interaction_id].append(msg)
                else:
                    standalone_messages.append(msg)
            
            # Build Interaction objects
            result: list[Interaction] = []
            
            # Add grouped interactions
            for interaction_id, msgs in interactions_dict.items():
                interaction = Interaction(
                    interaction_id=interaction_id,
                    thread_id=thread_id,
                    messages=[
                        Message(
                            id=m.id,
                            thread_id=m.thread_id,
                            interaction_id=m.interaction_id,
                            message=json.loads(m.message),
                            created_at=m.created_at
                        )
                        for m in msgs
                    ],
                    created_at=msgs[0].created_at.isoformat() if msgs else None
                )
                result.append(interaction)
            
            # Add standalone messages as single-message interactions
            for msg in standalone_messages:
                interaction = Interaction(
                    interaction_id=f"standalone-{msg.id}",
                    thread_id=thread_id,
                    messages=[
                        Message(
                            id=msg.id,
                            thread_id=msg.thread_id,
                            interaction_id=None,
                            message=json.loads(msg.message),
                            created_at=msg.created_at
                        )
                    ],
                    created_at=msg.created_at.isoformat()
                )
                result.append(interaction)
            
            # Sort all interactions by created_at
            result.sort(key=lambda x: x.created_at or "")
            
            return result

    def get_recent_interactions(self, thread_id: str, limit: int, messages_only: bool = False) -> list[Interaction]:
        """
        Retrieve the most recent N interactions for a given thread.
        
        Args:
            thread_id: Identifier for the conversation thread
            limit: Maximum number of interactions to return
            messages_only: If True, return only the messages in the Interactions, not the Interactions themselves
            
        Returns:
            List of the most recent Interaction objects, ordered from oldest to newest.
        """
        all_interactions = self.get_all_interactions(thread_id)
        if limit >= len(all_interactions):
            limit = len(all_interactions)
        interactions = all_interactions[-limit:]
        if messages_only:
            all_messages = [
                msg
                for interaction in interactions
                for msg in interaction.messages
                ]
            return all_messages
        else:
            return interactions
    
    def get_messages(
        self, 
        thread_id: str, 
        limit: Optional[int] = None
    ) -> list[dict]:
        """
        Retrieve messages for a given thread_id.
        
        Args:
            thread_id: Identifier for the conversation thread
            limit: Maximum number of messages to retrieve. If provided, returns
                   the most recent N messages. If None, returns all messages.
                   
        Returns:
            List of dictionaries containing message data, ordered from oldest to newest.
            Each dictionary contains: id, thread_id, message, and created_at.
        """
        with self.SessionLocal() as session:
            query = session.query(Message).filter(Message.thread_id == thread_id)
            
            if limit is not None:
                # Get the most recent N messages, but return them in chronological order
                messages = (
                    query.order_by(desc(Message.created_at))
                    .limit(limit)
                    .all()
                )
                # Reverse to get chronological order (oldest first)
                messages = list(reversed(messages))
            else:
                # Get all messages in chronological order
                messages = query.order_by(Message.created_at).all()
            
            return [
                {
                    "id": msg.id,
                    "thread_id": msg.thread_id,
                    "message": json.loads(msg.message),
                    "created_at": msg.created_at.isoformat()
                }
                for msg in messages
            ]

    @staticmethod
    def _message_has_user_prompt(message_entry: dict) -> bool:
        message = message_entry.get("message")
        if not isinstance(message, dict):
            return False
        parts = message.get("parts", [])
        if not isinstance(parts, list):
            return False
        return any(
            isinstance(part, dict) and part.get("part_kind") == "user-prompt"
            for part in parts
        )
    
    def get_thread_ids(self) -> list[str]:
        """
        Get a list of all unique thread IDs in the database.
        
        Returns:
            List of unique thread IDs
        """
        with self.SessionLocal() as session:
            result = session.query(Message.thread_id).distinct().all()
            return [row[0] for row in result]
    
    def count_messages(self, thread_id: Optional[str] = None) -> int:
        """
        Count the number of messages in the database.
        
        Args:
            thread_id: If provided, count messages only for this thread.
                       If None, count all messages.
                       
        Returns:
            Number of messages
        """
        with self.SessionLocal() as session:
            query = session.query(Message)
            if thread_id is not None:
                query = query.filter(Message.thread_id == thread_id)
            return query.count()
    
    def get_most_recent_thread_id(self) -> Optional[str]:
        """
        Get the thread_id of the most recently added message.
        
        Returns:
            The thread_id of the most recent message, or None if no messages exist.
        """
        with self.SessionLocal() as session:
            most_recent = (
                session.query(Message)
                .order_by(desc(Message.created_at))
                .first()
            )
            return most_recent.thread_id if most_recent else None

    @staticmethod
    def _extract_searchable_content(message: dict) -> str:
        """
        Extract searchable content from user-prompt and text parts only.
        
        This filters out non-content fields like provider_details, usage,
        tool-call, and tool-return parts.
        
        Args:
            message: The message dict containing parts
            
        Returns:
            Combined searchable text from user-prompt and text parts
        """
        parts = message.get("parts", [])
        searchable_parts = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_kind = part.get("part_kind", "")
            if part_kind in ("user-prompt", "text"):
                content = part.get("content", "")
                if content:
                    searchable_parts.append(content)
        return " ".join(searchable_parts)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """
        Tokenize text for BM25 indexing.
        
        Converts to lowercase and extracts word tokens.
        
        Args:
            text: Text to tokenize
            
        Returns:
            List of lowercase word tokens
        """
        text = text.lower()
        tokens = re.findall(r'\b\w+\b', text)
        return tokens

    def _get_all_messages_raw(
        self,
        thread_id: Optional[str] = None
    ) -> list[tuple[int, str, str, datetime]]:
        """
        Retrieve all messages as raw tuples, optionally filtered by thread_id.
        
        Args:
            thread_id: Optional thread to filter by. If None, returns all messages.
            
        Returns:
            List of tuples (id, thread_id, message_json, created_at)
        """
        with self.SessionLocal() as session:
            query = session.query(Message)
            if thread_id is not None:
                query = query.filter(Message.thread_id == thread_id)
            messages = query.order_by(Message.created_at).all()
            return [
                (msg.id, msg.thread_id, msg.message, msg.created_at)
                for msg in messages
            ]

    def _get_message_pair(
        self,
        message_id: int,
        all_messages: list[tuple[int, str, str, datetime]]
    ) -> list[tuple[int, str, str, datetime]]:
        """
        Get the user-assistant message pair containing the given message.
        
        For a user message (request), returns it plus the following response.
        For an assistant message (response), returns the preceding request plus it.
        
        Args:
            message_id: The ID of the matched message
            all_messages: List of all messages as tuples
            
        Returns:
            List of message tuples forming the conversation pair
        """
        # Find the index of the message
        idx = None
        for i, (mid, _, _, _) in enumerate(all_messages):
            if mid == message_id:
                idx = i
                break
        
        if idx is None:
            return []
        
        msg_id, thread_id, message_json, created_at = all_messages[idx]
        message = json.loads(message_json)
        kind = message.get("kind", "")
        
        pair = []
        if kind == "request":
            # This is a user message, include it and the next response if exists
            pair.append(all_messages[idx])
            if idx + 1 < len(all_messages):
                next_msg = json.loads(all_messages[idx + 1][2])
                if next_msg.get("kind") == "response":
                    pair.append(all_messages[idx + 1])
        elif kind == "response":
            # This is an assistant message, include previous request and this
            if idx > 0:
                prev_msg = json.loads(all_messages[idx - 1][2])
                if prev_msg.get("kind") == "request":
                    pair.append(all_messages[idx - 1])
            pair.append(all_messages[idx])
        else:
            # Unknown kind, just return this message
            pair.append(all_messages[idx])
        
        return pair

    @staticmethod
    def _format_message_pair(
        pair: list[tuple[int, str, str, datetime]]
    ) -> str:
        """
        Format a message pair as a human-readable string for LLM consumption.
        
        Args:
            pair: List of message tuples (id, thread_id, message_json, created_at)
            
        Returns:
            Formatted string with datetime and role-labeled content
        """
        if not pair:
            return ""
        
        # Use the timestamp from the first message in the pair
        _, _, _, created_at = pair[0]
        formatted_time = created_at.strftime("%Y-%m-%d %H:%M")
        
        lines = [f"--- Conversation from {formatted_time} ---"]
        
        for _, _, message_json, _ in pair:
            message = json.loads(message_json)
            kind = message.get("kind", "")
            content = MessageHistory._extract_searchable_content(message)
            
            if kind == "request":
                role = "User"
            elif kind == "response":
                role = "Assistant"
            else:
                role = "Unknown"
            
            if content:
                lines.append(f"{role}: {content}")
        
        return "\n".join(lines)

    def search(
        self,
        query: str,
        thread_id: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.5,
    ) -> str:
        """
        Search message history using BM25 ranking.
        
        Searches through user prompts and assistant text responses,
        ignoring tool calls, tool returns, provider details, and usage data.
        
        Args:
            query: Search query string
            thread_id: Optional thread to search within (searches all if None)
            limit: Maximum number of conversation pairs to return
            
        Returns:
            Human-readable formatted string of matching conversation fragments,
            suitable for LLM input. Returns empty string if no matches found.
        """
        # Get all messages
        all_messages = self._get_all_messages_raw(thread_id)
        
        if not all_messages:
            return ""
        
        # Build corpus for BM25
        corpus = []
        message_ids = []
        for msg_id, _, message_json, _ in all_messages:
            message = json.loads(message_json)
            content = self._extract_searchable_content(message)
            if content.strip():  # Only index messages with searchable content
                corpus.append(self._tokenize(content))
                message_ids.append(msg_id)
        
        if not corpus:
            return ""
        
        # Create BM25 index and search
        bm25 = BM25Okapi(corpus)
        query_tokens = self._tokenize(query)
        scores = bm25.get_scores(query_tokens)
        
        # Get top scoring messages
        scored_indices = [(i, score) for i, score in enumerate(scores) if score > min_score]
        scored_indices.sort(key=lambda x: x[1], reverse=True)
        
        # Collect unique message pairs (avoid duplicates if both request and response match)
        seen_pair_ids = set()
        result_pairs = []
        
        for idx, _ in scored_indices:
            if len(result_pairs) >= limit:
                break
            
            msg_id = message_ids[idx]
            pair = self._get_message_pair(msg_id, all_messages)
            
            if not pair:
                continue
            
            # Create a unique identifier for this pair
            pair_id = tuple(m[0] for m in pair)
            if pair_id in seen_pair_ids:
                continue
            
            seen_pair_ids.add(pair_id)
            result_pairs.append(pair)
        
        # Format results
        formatted_results = []
        for pair in result_pairs:
            formatted = self._format_message_pair(pair)
            if formatted:
                formatted_results.append(formatted)
        
        return "\n\n".join(formatted_results)

if __name__ == "__main__":
    message_history = MessageHistory(db_path="sb3datadir/message_history.db")
    print(message_history.search("git error"))