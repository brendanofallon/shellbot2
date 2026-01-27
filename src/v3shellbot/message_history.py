"""
Module for storing conversation history in a SQLite database using SQLAlchemy.

This module provides an append-only message history storage system where messages
can be added but not edited or removed.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
import json
import logging

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
        message: JSON formatted string containing the message content
        created_at: Timestamp when the message was added
    """
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String, nullable=False, index=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    def __repr__(self) -> str:
        return f"<Message(id={self.id}, thread_id={self.thread_id}, created_at={self.created_at})>"


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

    def get_messages_starting_with_user_prompt(
        self,
        thread_id: str,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        Retrieve messages ensuring the first entry includes a user-prompt part.

        This fetches up to twice the requested number of messages, then trims the
        list to start at the earliest message that includes a user-prompt part.
        """
        effective_limit = None if limit is None else limit * 2
        messages = self.get_messages(thread_id, limit=effective_limit)
        for index, message_entry in enumerate(messages):
            if self._message_has_user_prompt(message_entry):
                return messages[index:]
        return messages
    
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
