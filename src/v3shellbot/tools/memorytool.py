"""
MemoryTool - A simple key-value store backed by the filesystem.

Keys are short, human-readable descriptions stored as filenames.
Values are larger text blobs stored as file contents.
"""

import logging
import os
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

from v3shellbot.tools.util import classproperty

class MemoryTool:
    """
    A filesystem-backed key-value store for storing and retrieving text data.
    
    Keys are used as filenames and should be short, human-readable descriptions.
    Values are stored as the contents of these files.
    """
    
    def __init__(self, storage_dir: Optional[str] = None):
        """
        Initialize the MemoryTool.
        
        Args:
            storage_dir: Directory to store key-value pairs. Defaults to ~/.shellbot/memory
        """
        self.storage_dir = storage_dir
        if storage_dir is None:
            self.storage_dir = Path(os.getenv("SHELLBOT_DATADIR", "~/.shellbot2")).expanduser() / "memory"
        
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"MemoryTool initialized with storage directory: {self.storage_dir}")
    
    def _sanitize_key(self, key: str) -> str:
        """
        Sanitize a key to be a valid filename and prevent path traversal.
        
        Replaces characters that aren't filesystem-safe with underscores.
        Ensures the key cannot reference files outside the storage directory.
        """
        # First strip all whitespace and dots from edges
        sanitized = key.strip()
        
        # Replace path separators and other problematic characters
        invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|', '\n', '\r', '\t']
        for char in invalid_chars:
            sanitized = sanitized.replace(char, '_')
        
        # Remove leading/trailing whitespace, underscores, and dots again after replacements
        sanitized = sanitized.strip('._  ')
        
        # Replace any remaining dots to prevent .. or hidden files
        # Allow dots in the middle of names but not at start/end or multiple consecutive
        sanitized = sanitized.replace('..', '_')
        
        # Remove any leading dots to prevent hidden files
        while sanitized.startswith('.'):
            sanitized = sanitized[1:]
        
        # Final trim to remove any trailing dots or whitespace
        sanitized = sanitized.strip('. ')
        
        # Ensure the key is not empty or only whitespace/special chars
        if not sanitized or sanitized.replace('_', '').strip() == '':
            raise ValueError("Key cannot be empty after sanitization")
        
        # Additional safety: ensure no path-like components remain
        if sanitized in ['.', '..']:
            raise ValueError("Key cannot be '.' or '..'")
        
        return sanitized
    
    def _get_file_path(self, key: str) -> Path:
        """
        Get the full file path for a given key.
        
        Ensures the resulting path is within the storage directory to prevent
        path traversal attacks.
        """
        sanitized_key = self._sanitize_key(key)
        file_path = self.storage_dir / f"{sanitized_key}.txt"
        
        # Resolve to absolute path and verify it's within storage_dir
        try:
            resolved_path = file_path.resolve()
            resolved_storage = self.storage_dir.resolve()
            
            # Check if the resolved path is within the storage directory
            # Use relative_to to verify containment
            resolved_path.relative_to(resolved_storage)
            
        except (ValueError, RuntimeError) as e:
            # relative_to raises ValueError if path is not relative to storage_dir
            raise ValueError(
                f"Invalid key: resolved path would be outside storage directory"
            ) from e
        
        return file_path
    
    def list_keys(self) -> List[str]:
        """
        List all keys currently stored.
        
        Returns:
            List of key names (filenames without .txt extension)
        """
        try:
            keys = []
            for file_path in self.storage_dir.glob("*.txt"):
                # Remove .txt extension to get the key
                key = file_path.stem
                keys.append(key)
            
            logger.info(f"Listed {len(keys)} keys from storage")
            return sorted(keys)
        except Exception as e:
            logger.error(f"Error listing keys: {e}")
            raise
    
    def insert(self, key: str, value: str) -> bool:
        """
        Insert a new key-value pair. Fails if the key already exists.
        
        Args:
            key: The key (will be used as filename)
            value: The value to store (file contents)
        
        Returns:
            True if insertion was successful
        
        Raises:
            ValueError: If the key already exists
        """
        try:
            file_path = self._get_file_path(key)
            
            if file_path.exists():
                raise ValueError(f"Key '{key}' already exists. Use replace() to update existing keys.")
            
            file_path.write_text(value, encoding='utf-8')
            logger.info(f"Inserted new key: {key}")
            return True
        except Exception as e:
            logger.error(f"Error inserting key '{key}': {e}")
            raise
    
    def replace(self, key: str, value: str) -> bool:
        """
        Replace an existing key-value pair. Fails if the key doesn't exist.
        
        Args:
            key: The key to replace
            value: The new value to store
        
        Returns:
            True if replacement was successful
        
        Raises:
            ValueError: If the key doesn't exist
        """
        try:
            file_path = self._get_file_path(key)
            
            if not file_path.exists():
                raise ValueError(f"Key '{key}' does not exist. Use insert() to create new keys.")
            
            file_path.write_text(value, encoding='utf-8')
            logger.info(f"Replaced key: {key}")
            return True
        except Exception as e:
            logger.error(f"Error replacing key '{key}': {e}")
            raise
    
    def get(self, key: str) -> str:
        """
        Retrieve the value for a given key.
        
        Args:
            key: The key to retrieve
        
        Returns:
            The value associated with the key
        
        Raises:
            ValueError: If the key doesn't exist
        """
        try:
            file_path = self._get_file_path(key)
            
            if not file_path.exists():
                raise ValueError(f"Key '{key}' does not exist")
            
            value = file_path.read_text(encoding='utf-8')
            logger.info(f"Retrieved key: {key}")
            return value
        except Exception as e:
            logger.error(f"Error retrieving key '{key}': {e}")
            raise
    
    def delete(self, key: str) -> bool:
        """
        Delete a key-value pair.
        
        Args:
            key: The key to delete
        
        Returns:
            True if deletion was successful
        
        Raises:
            ValueError: If the key doesn't exist
        """
        try:
            file_path = self._get_file_path(key)
            
            if not file_path.exists():
                raise ValueError(f"Key '{key}' does not exist")
            
            file_path.unlink()
            logger.info(f"Deleted key: {key}")
            return True
        except Exception as e:
            logger.error(f"Error deleting key '{key}': {e}")
            raise
    
    def exists(self, key: str) -> bool:
        """
        Check if a key exists.
        
        Args:
            key: The key to check
        
        Returns:
            True if the key exists, False otherwise
        """
        try:
            file_path = self._get_file_path(key)
            return file_path.exists()
        except Exception as e:
            logger.error(f"Error checking existence of key '{key}': {e}")
            return False
    
    def get_all(self) -> Dict[str, str]:
        """
        Retrieve all key-value pairs.
        
        Returns:
            Dictionary of all keys and their values
        """
        try:
            result = {}
            for key in self.list_keys():
                result[key] = self.get(key)
            
            logger.info(f"Retrieved all {len(result)} key-value pairs")
            return result
        except Exception as e:
            logger.error(f"Error retrieving all key-value pairs: {e}")
            raise


# Bot function wrapper for integration with the assistant
class MemoryFunction:
    """

    Function wrapper for MemoryTool to integrate with the bot's function calling system.
    """
    
    def __init__(self, storage_dir: Optional[str] = None):
        self.memory_tool = MemoryTool(storage_dir)
    
    @property
    def name(self):
        return "memory"
    
    @classproperty
    def toolname(cls):
        return "memory"
    
    @property
    def description(self):
        return """This function manages a persistent key-value memory store for information about user preferences, projects, and related information.
        It supports the following operations:
        - 'list': List all stored keys
        - 'insert': Insert a new key-value pair (fails if key exists)
        - 'replace': Replace an existing key-value pair (fails if key doesn't exist)
        - 'get': Retrieve the value for a given key
        - 'delete': Delete a key-value pair
        
        Keys should be short, human-readable descriptions of the value text, like "how_to_build_jenever" or "favorite_space_westerns".
        Keys must contain only alphanumeric characters, underscores, or dashes. Values can be larger text blobs.
        """
    
    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "The operation to perform",
                    "enum": ["list", "insert", "replace", "get", "delete", "exists"]
                },
                "key": {
                    "type": "string",
                    "description": "The key for the operation (not required for 'list' operation)"
                },
                "value": {
                    "type": "string",
                    "description": "The value to store (required for 'insert' and 'replace' operations)"
                }
            },
            "required": ["operation"]
        }
    
    def __call__(self, **kwargs):
        operation = kwargs.get("operation")
        key = kwargs.get("key")
        value = kwargs.get("value")
        
        try:
            if operation == "list":
                keys = self.memory_tool.list_keys()
                if not keys:
                    return "No keys stored in memory"
                return f"Stored keys ({len(keys)}):\n" + "\n".join(f"  - {k}" for k in keys)
            
            elif operation == "insert":
                if not key:
                    return "Error: 'key' parameter is required for insert operation"
                if not value:
                    return "Error: 'value' parameter is required for insert operation"
                self.memory_tool.insert(key, value)
                return f"Successfully inserted key: {key}"
            
            elif operation == "replace":
                if not key:
                    return "Error: 'key' parameter is required for replace operation"
                if not value:
                    return "Error: 'value' parameter is required for replace operation"
                self.memory_tool.replace(key, value)
                return f"Successfully replaced key: {key}"
            
            elif operation == "get":
                if not key:
                    return "Error: 'key' parameter is required for get operation"
                value = self.memory_tool.get(key)
                return f"Value for key '{key}':\n{value}"
            
            elif operation == "delete":
                if not key:
                    return "Error: 'key' parameter is required for delete operation"
                self.memory_tool.delete(key)
                return f"Successfully deleted key: {key}"
            
            elif operation == "exists":
                if not key:
                    return "Error: 'key' parameter is required for exists operation"
                exists = self.memory_tool.exists(key)
                return f"Key '{key}' {'exists' if exists else 'does not exist'}"
            
            else:
                return f"Error: Unknown operation '{operation}'"
        
        except Exception as e:
            return f"Error performing {operation} operation: {str(e)}"


if __name__ == "__main__":
    # Example usage
    memory = MemoryTool(storage_dir="./test_memory")
    
    # Insert some data
    memory.insert("project_notes", "This is a test project for building a memory tool")
    memory.insert("meeting_summary", "Discussed the new features for Q1 2024")
    
    # List all keys
    print("All keys:", memory.list_keys())
    
    # Get a value
    print("\nProject notes:", memory.get("project_notes"))
    
    # Replace a value
    memory.replace("project_notes", "Updated project notes with new information")
    print("\nUpdated project notes:", memory.get("project_notes"))
    
    # Check existence
    print("\nDoes 'project_notes' exist?", memory.exists("project_notes"))
    print("Does 'nonexistent' exist?", memory.exists("nonexistent"))
    
    # Get all
    print("\nAll key-value pairs:", memory.get_all())
    
    # Clean up test directory
    import shutil
    shutil.rmtree("./test_memory")

