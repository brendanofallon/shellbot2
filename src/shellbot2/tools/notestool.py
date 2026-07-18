import os
from pathlib import Path
from datetime import datetime

from shellbot2.tools.tool_spec import ToolSpec
from shellbot2.tools.util import classproperty


def _format_note(path: Path, content: str, mtime: float) -> str:
    mod_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"path: {path}\n"
        f"last_modified: {mod_time}\n"
        f"---\n"
        f"{content}"
    )


class NotesTool:

    def __init__(self, notes_dir: str = None):
        if notes_dir is None:
            notes_dir = os.environ.get("SHELLBOT_NOTES_DIR", "~/notes")
        self.notes_dir = Path(notes_dir).expanduser().resolve()

    @property
    def name(self):
        return "notes"

    @classproperty
    def toolname(cls):
        return "notes"

    @property
    def description(self):
        return (
            "Search and list personal notes stored as text files. "
            "Notes live in ~/notes and may be nested in subdirectories. "
            "Supported operations:\n"
            "- 'search': Find notes whose filename or content matches ALL of the given keywords (case-insensitive). "
            "Returns the full content, file path, and last-modified time for every match.\n"
            "- 'list': List every note with its path and last-modified time (content is omitted for brevity)."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "The operation to perform",
                    "enum": ["search", "list"],
                },
                "keywords": {
                    "type": "string",
                    "description": (
                        "Space-separated keywords to search for. "
                        "A note matches only if ALL keywords appear in its filename or content (case-insensitive). "
                        "Required when operation is 'search'."
                    ),
                },
            },
            "required": ["operation"],
        }

    def _iter_notes(self):
        if not self.notes_dir.exists():
            return
        for path in sorted(self.notes_dir.rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                yield path

    def _read_note(self, path: Path) -> str | None:
        try:
            return path.read_text(errors="replace")
        except OSError:
            return None

    def _search(self, keywords: str) -> str:
        tokens = keywords.lower().split()
        if not tokens:
            return "No keywords provided for search."

        matches = []
        for path in self._iter_notes():
            content = self._read_note(path)
            if content is None:
                continue
            searchable = f"{path.name}\n{content}".lower()
            if all(tok in searchable for tok in tokens):
                matches.append(_format_note(path, content, path.stat().st_mtime))

        if not matches:
            return f"No notes matched all keywords: {keywords}"
        header = f"Found {len(matches)} matching note(s):\n"
        return header + "\n\n==========\n\n".join(matches)

    def _list(self) -> str:
        entries = []
        for path in self._iter_notes():
            mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            entries.append(f"  {path}  (modified {mtime})")

        if not entries:
            return f"No notes found in {self.notes_dir}"
        return f"Notes ({len(entries)}):\n" + "\n".join(entries)

    def __call__(self, **kwargs):
        operation = kwargs.get("operation")
        if not operation:
            return "The 'operation' parameter is required."

        if operation == "search":
            keywords = kwargs.get("keywords")
            if not keywords:
                return "The 'keywords' parameter is required for the 'search' operation."
            return self._search(keywords)
        elif operation == "list":
            return self._list()
        else:
            return f"Unknown operation: {operation}"


TOOL_SPECS = (
    ToolSpec(
        name="notes",
        description=(
            "Search or list personal text notes stored in a configured notes "
            "directory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "The operation to perform.",
                    "enum": ["search", "list"],
                },
                "keywords": {
                    "type": "string",
                    "description": "Space-separated keywords; required for search.",
                },
            },
            "required": ["operation"],
        },
        factory=lambda _runtime, kwargs: NotesTool(**kwargs),
    ),
)
