from typing import Optional
from pathlib import Path

from shellbot2.tools.util import classproperty
from shellbot2.message_history import MessageHistory


class ConversationSearchTool:
    """
    Tool for searching conversation history using BM25 ranking.
    
    This tool searches through stored message history and returns relevant
    conversation fragments matching the query.
    """
    
    def __init__(
        self,
        message_history: Optional[MessageHistory] = None,
        db_path: Optional[str | Path] = None
    ):
        """
        Initialize the ConversationSearchTool.
        
        Args:
            message_history: An existing MessageHistory instance to use.
            db_path: Path to the SQLite database file. Used to create a new
                     MessageHistory if message_history is not provided.
                     
        Raises:
            ValueError: If neither message_history nor db_path is provided.
        """
        if message_history is not None:
            self.message_history = message_history
        elif db_path is not None:
            self.message_history = MessageHistory(db_path=db_path)
        else:
            raise ValueError(
                "Either message_history or db_path must be provided"
            )
    
    @property
    def name(self):
        return "conversation-search"
    
    @classproperty
    def toolname(cls):
        return "conversation-search"
    
    @property
    def description(self):
        return """This function searches through past conversation history to find relevant messages. 
        It uses BM25 ranking to find conversations that match the query.
        The search covers user prompts and assistant responses, excluding tool calls and other metadata.
        Results are returned as formatted conversation fragments with timestamps, showing the user-assistant exchange.
        Use this to recall previous discussions, find past answers, or reference earlier conversations."""
    
    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find relevant conversations. Should be a few short keywords suitable as input for BM25 search.g"
                }
            },
            "required": ["query"]
        }
    
    def __call__(self, **kwargs) -> str:
        query = kwargs.get("query")
        
        if not query:
            return f"The function {self.name} requires a 'query' keyword argument, but didn't get one"
        
        results = self.message_history.search(query)
        
        if not results:
            return "No matching conversations found for the given query."
        
        return results


if __name__ == "__main__":
    tool = ConversationSearchTool(db_path="sb3datadir/message_history.db")
    print(tool(query="git error"))
