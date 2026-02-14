
import glob
import logging
import os
import subprocess

from shellbot2.tools.util import classproperty

logger = logging.getLogger(__name__)


class FileSearchFunction:
    """Search files for a regex pattern using grep, returning matches with surrounding context."""

    @property
    def name(self):
        return "file_search"

    @classproperty
    def toolname(cls):
        return "file_search"

    @property
    def description(self):
        return (
            "Search one or more files or file glob patterns for a regular expression. "
            "Returns matching lines along with surrounding context lines (default 5 lines "
            "before and after each match). Supports standard grep-style regular expressions. "
            "The 'paths' parameter accepts a list of file paths or glob patterns "
            "(e.g. ['src/**/*.py', 'README.md'])."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The regular expression pattern to search for.",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "A list of file paths or glob patterns to search. "
                        "Glob patterns like '*.py' or 'src/**/*.py' are expanded automatically."
                    ),
                },
                "context_lines": {
                    "type": "integer",
                    "description": (
                        "Number of context lines to show before and after each match. "
                        "Defaults to 5."
                    ),
                },
            },
            "required": ["pattern", "paths"],
        }

    def _expand_paths(self, paths):
        """Expand glob patterns and return a list of individual file paths."""
        expanded = []
        for p in paths:
            p = os.path.expanduser(p)
            # If it looks like a glob pattern, expand it
            if any(c in p for c in ("*", "?", "[")):
                matches = glob.glob(p, recursive=True)
                expanded.extend(m for m in matches if os.path.isfile(m))
            else:
                if os.path.isfile(p):
                    expanded.append(p)
                elif os.path.isdir(p):
                    # If a directory is given, search recursively inside it
                    expanded.append(p)
        return expanded

    def __call__(self, **kwargs):
        pattern = kwargs.get("pattern")
        if not pattern:
            return "Error: the 'pattern' parameter is required for file_search."

        paths = kwargs.get("paths")
        if not paths:
            return "Error: the 'paths' parameter is required for file_search."

        context_lines = kwargs.get("context_lines", 5)

        expanded = self._expand_paths(paths)
        if not expanded:
            return f"No files matched the given paths: {paths}"

        cmd = [
            "grep",
            "-r",           # recursive (useful when a directory is passed)
            "-n",           # show line numbers
            "-E",           # extended regex
            f"-C{context_lines}",
            "--",
            pattern,
        ] + expanded

        logger.info(f"Running file search: grep -rnE -C{context_lines} '{pattern}' across {len(expanded)} path(s)")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 1:
            return f"No matches found for pattern '{pattern}' in the specified files."
        if result.returncode not in (0, 1):
            return f"grep returned exit code {result.returncode}.\nStderr: {result.stderr}"

        output = result.stdout
        # Truncate very large outputs to avoid overwhelming the context window
        max_len = 50000
        if len(output) > max_len:
            output = output[:max_len] + f"\n\n... (output truncated at {max_len} characters)"

        return output


class TextReplaceFunction:
    """Replace exact text strings within a single file."""

    @property
    def name(self):
        return "text_replace"

    @classproperty
    def toolname(cls):
        return "text_replace"

    @property
    def description(self):
        return (
            "Replace occurrences of an exact text string with a new string in a single file. "
            "This performs a literal string replacement (not regex). Useful for making code edits."
            "Returns the number of replacements made and a brief summary of the changes."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to modify.",
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text string to search for and replace.",
                },
                "new_text": {
                    "type": "string",
                    "description": "The text string to replace the old text with.",
                },
            },
            "required": ["file_path", "old_text", "new_text"],
        }

    def __call__(self, **kwargs):
        file_path = kwargs.get("file_path")
        if not file_path:
            return "Error: the 'file_path' parameter is required for text_replace."

        old_text = kwargs.get("old_text")
        if old_text is None:
            return "Error: the 'old_text' parameter is required for text_replace."

        new_text = kwargs.get("new_text")
        if new_text is None:
            return "Error: the 'new_text' parameter is required for text_replace."

        file_path = os.path.expanduser(file_path)

        if not os.path.isfile(file_path):
            return f"Error: file not found: {file_path}"

        with open(file_path, "r") as f:
            content = f.read()

        count = content.count(old_text)
        if count == 0:
            return f"No occurrences of the specified text were found in {file_path}."

        new_content = content.replace(old_text, new_text)

        with open(file_path, "w") as f:
            f.write(new_content)

        logger.info(f"Replaced {count} occurrence(s) in {file_path}")
        return f"Successfully replaced {count} occurrence(s) in {file_path}."
