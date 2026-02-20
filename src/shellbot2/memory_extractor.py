"""
Memory extraction module that analyzes conversation history to extract and store
user-relevant information as persistent memories.

Uses a pydantic-ai Agent with structured output to identify:
- Projects the user is working on (names, technologies, specific tasks)
- Interests and hobbies (topics, activities, research)
- Personal facts (family, pets, location, preferences)

Extracted memories are stored via the MemoryTool's filesystem-backed key-value store.
"""

import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from shellbot2.agent import initialize_bedrock_model, load_conf
from shellbot2.message_history import MessageHistory
from shellbot2.tools.memorytool import MemoryTool

logger = logging.getLogger(__name__)


EXTRACTION_INSTRUCTIONS = """\
You are a memory extraction agent. Your job is to analyze conversation history \
between a user and an AI assistant and extract important information that should \
be remembered for future conversations.

Extract information in these categories:

1. **Projects**: What the user is working on, including project names, technologies, \
specific tasks or features they're developing. Be specific about what aspect of the \
project they're working on.
   Example: "User is working on a tool called shellbot2, specifically the subtask \
facility, which manages long-running background tasks"

2. **Interests**: Topics the user shows interest in, hobbies, things they're \
researching or planning.
   Example: "User is interested in resorts in Mexico and travel there"
   Example: "User is researching swim lessons for young children in Salt Lake City"

3. **Personal Facts**: Facts about the user's life, family, pets, preferences, \
location, etc.
   Example: "The user has two dogs, including a golden retriever"
   Example: "The user lives in Salt Lake City"

Guidelines:
- Only extract information that seems genuinely useful to remember for future conversations.
- Be specific and detailed — include names, locations, technologies, etc.
- Write each memory as a concise but complete statement about the user.
- Use descriptive keys that summarize the memory content \
(e.g., "shellbot2_subtask_project", "mexico_travel_interest").
- Keys should use underscores, be lowercase, and be short but descriptive.
- If the conversation doesn't contain any extractable information, return an empty list.
- Do NOT extract information about the assistant's capabilities or generic knowledge.
- DO extract information that reveals something specific about this particular user.
- If the existing memories already cover a piece of information, do NOT extract it again \
unless the conversation contains a meaningful update to that information. If there IS an \
update, use the SAME key as the existing memory so it gets replaced.
"""


class ExtractedMemory(BaseModel):
    """A single extracted memory from conversation history."""

    key: str = Field(
        description="Short, descriptive key for the memory (lowercase, underscores, no spaces)"
    )
    value: str = Field(
        description="The memory content — a concise statement about the user"
    )
    category: str = Field(
        description="Category: 'project', 'interest', or 'fact'"
    )


class ExtractionResult(BaseModel):
    """Result of memory extraction from conversation history."""

    memories: list[ExtractedMemory] = Field(
        default_factory=list,
        description="List of extracted memories. Empty if no useful information found.",
    )


class MemoryExtractor:
    """
    Extracts user-relevant information from conversation history and stores it
    as memories using the MemoryTool.

    Uses a pydantic-ai Agent with structured output to identify projects,
    interests, and personal facts from recent conversations.
    """

    def __init__(
        self,
        message_history: MessageHistory,
        memory_tool: MemoryTool,
        model: str = "google-gla:gemini-2.0-flash",
        conf: Optional[dict] = None,
    ):
        """
        Initialize the MemoryExtractor.

        Args:
            message_history: The conversation history store to read from.
            memory_tool: The memory store to write extracted memories to.
            model: Model string for the pydantic-ai Agent (used when provider is not bedrock).
            conf: Optional configuration dict (same format as agent_conf.yaml).
                  If provided and provider is 'bedrock', the bedrock model will be used.
        """
        self.message_history = message_history
        self.memory_tool = memory_tool
        self.conf = conf or {}
        self.agent = self._initialize_agent(model)

    def _initialize_agent(self, model_name: str) -> Agent:
        """
        Initialize the pydantic-ai extraction agent.

        Follows the same model initialization pattern as agent.py: if conf specifies
        provider='bedrock', a BedrockConverseModel is created; otherwise the model
        string is passed directly.
        """
        if self.conf.get("provider") == "bedrock":
            bedrock_conf = self.conf.get("bedrock", {})
            model = initialize_bedrock_model(
                self.conf.get("model", model_name),
                bedrock_conf.get("region_name", "us-west-2"),
            )
        else:
            model = self.conf.get("model", model_name)

        return Agent(
            model,
            instructions=EXTRACTION_INSTRUCTIONS,
            output_type=ExtractionResult,
        )

    def _format_interactions_for_extraction(
        self, thread_id: str, limit: int = 10
    ) -> str:
        """
        Retrieve and format recent interactions as readable text for the extraction agent.

        Args:
            thread_id: The conversation thread to extract from.
            limit: Maximum number of recent interactions to process.

        Returns:
            Formatted string of recent conversations.
        """
        interactions = self.message_history.get_recent_interactions(
            thread_id, limit=limit
        )

        formatted_parts = []
        for interaction in interactions:
            for msg in interaction.messages:
                message = msg.message if isinstance(msg.message, dict) else {}
                content = MessageHistory._extract_searchable_content(message)
                if not content.strip():
                    continue
                kind = message.get("kind", "")
                if kind == "request":
                    role = "User"
                elif kind == "response":
                    role = "Assistant"
                else:
                    continue
                formatted_parts.append(f"{role}: {content}")

        return "\n\n".join(formatted_parts)

    def _get_existing_memories_summary(self) -> str:
        """
        Get a summary of existing memories so the agent can avoid duplicates.

        Returns:
            Formatted string listing all existing memory keys and values.
        """
        all_memories = self.memory_tool.get_all()
        if not all_memories:
            return "No existing memories."

        lines = ["Existing memories:"]
        for key, value in all_memories.items():
            lines.append(f"  - {key}: {value}")
        return "\n".join(lines)

    async def extract_and_store(
        self, thread_id: str, interaction_limit: int = 10
    ) -> list[ExtractedMemory]:
        """
        Extract memories from recent conversation history and store them.

        Reads the most recent interactions from the given thread, sends them to
        the extraction agent along with existing memories for deduplication,
        and stores any new or updated memories via the MemoryTool.

        Args:
            thread_id: The conversation thread to analyze.
            interaction_limit: Number of recent interactions to process.

        Returns:
            List of ExtractedMemory objects that were stored.
        """
        conversation_text = self._format_interactions_for_extraction(
            thread_id, limit=interaction_limit
        )
        if not conversation_text.strip():
            logger.info("No conversation content to extract memories from")
            return []

        existing_memories = self._get_existing_memories_summary()

        prompt = (
            f"Here are the existing memories:\n\n{existing_memories}\n\n"
            f"---\n\n"
            f"Here is the recent conversation to analyze:\n\n{conversation_text}\n\n"
            f"---\n\n"
            f"Extract any new or updated information about the user from this conversation. "
            f"If the conversation does not contain any new information worth remembering, "
            f"return an empty list."
        )

        logger.info(f"Running memory extraction for thread {thread_id}")
        result = await self.agent.run(prompt)
        extraction = result.output

        stored_memories = []
        for memory in extraction.memories:
            if self.memory_tool.exists(memory.key):
                self.memory_tool.replace(memory.key, memory.value)
                logger.info(f"Updated existing memory: {memory.key}")
            else:
                self.memory_tool.insert(memory.key, memory.value)
                logger.info(f"Inserted new memory: {memory.key}")
            stored_memories.append(memory)

        logger.info(f"Extracted and stored {len(stored_memories)} memories")
        return stored_memories


async def run_extraction(datadir: Path, thread_id: Optional[str] = None) -> list[ExtractedMemory]:
    """
    Convenience function to run memory extraction for a given data directory.

    Loads configuration, initializes dependencies, and runs extraction on the
    most recent (or specified) thread.

    Args:
        datadir: Path to the shellbot data directory containing agent_conf.yaml
                 and the message_history database.
        thread_id: Optional thread ID to extract from. If None, uses the most
                   recent thread.

    Returns:
        List of extracted and stored memories.
    """
    conf = load_conf(datadir)
    message_history = MessageHistory(datadir / "message_history.db")
    memory_tool = MemoryTool()

    if thread_id is None:
        thread_id = message_history.get_most_recent_thread_id()
        if thread_id is None:
            logger.warning("No threads found in message history")
            return []

    extractor = MemoryExtractor(
        message_history=message_history,
        memory_tool=memory_tool,
        conf=conf,
    )

    return await extractor.extract_and_store(thread_id)


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)
    datadir = Path("sb3datadir")
    memories = asyncio.run(run_extraction(datadir))
    for m in memories:
        print(f"[{m.category}] {m.key}: {m.value}")
