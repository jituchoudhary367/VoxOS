"""
Intent classification helpers for the Listener Agent.

Provides both fast-path deterministic mapping and LLM-assisted classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IntentDefinition:
    """Metadata about a known intent."""
    name: str
    description: str
    requires_execution: bool = True
    example_phrases: list[str] = field(default_factory=list)


# Canonical intent registry
KNOWN_INTENTS: dict[str, IntentDefinition] = {
    "get_current_time": IntentDefinition(
        name="get_current_time",
        description="Get the current date and/or time",
        example_phrases=["what time is it", "current time", "what's the date"],
    ),
    "get_system_info": IntentDefinition(
        name="get_system_info",
        description="Get system hardware/OS information",
        example_phrases=["system info", "what's my RAM", "CPU usage"],
    ),
    "get_resources": IntentDefinition(
        name="get_resources",
        description="Get CPU, memory, disk usage",
        example_phrases=["check CPU", "memory usage", "disk space"],
    ),
    "open_application": IntentDefinition(
        name="open_application",
        description="Open a named application",
        example_phrases=["open chrome", "launch notepad", "start spotify"],
    ),
    "close_application": IntentDefinition(
        name="close_application",
        description="Close a running application",
        example_phrases=["close chrome", "quit notepad"],
    ),
    "list_applications": IntentDefinition(
        name="list_applications",
        description="List running applications",
        example_phrases=["what apps are running", "list processes"],
    ),
    "search_web": IntentDefinition(
        name="search_web",
        description="Search the web for information",
        example_phrases=["search for", "google", "find information about"],
    ),
    "browse_url": IntentDefinition(
        name="browse_url",
        description="Navigate to a URL",
        example_phrases=["go to", "open website", "navigate to"],
    ),
    "list_files": IntentDefinition(
        name="list_files",
        description="List files in a directory",
        example_phrases=["list files", "what files", "show folder contents"],
    ),
    "read_file": IntentDefinition(
        name="read_file",
        description="Read the contents of a file",
        example_phrases=["read file", "show file", "open file"],
    ),
    "create_file": IntentDefinition(
        name="create_file",
        description="Create a new file",
        example_phrases=["create file", "make file", "new file"],
    ),
    "delete_file": IntentDefinition(
        name="delete_file",
        description="Delete a file or directory",
        example_phrases=["delete file", "remove file", "erase"],
    ),
    "move_file": IntentDefinition(
        name="move_file",
        description="Move or rename a file",
        example_phrases=["move file", "rename file"],
    ),
    "run_command": IntentDefinition(
        name="run_command",
        description="Execute a shell command",
        example_phrases=["run command", "execute", "terminal"],
    ),
    "take_screenshot": IntentDefinition(
        name="take_screenshot",
        description="Take a screenshot of the screen",
        example_phrases=["take screenshot", "capture screen", "screenshot"],
    ),
    "research_topic": IntentDefinition(
        name="research_topic",
        description="Research a topic using multiple sources",
        example_phrases=["research", "find out about", "investigate"],
    ),
    "create_document": IntentDefinition(
        name="create_document",
        description="Create a document (PDF, Word, etc.)",
        example_phrases=["create document", "write report", "make PDF"],
    ),
    "summarize_document": IntentDefinition(
        name="summarize_document",
        description="Summarize a document",
        example_phrases=["summarize", "give me a summary", "TLDR"],
    ),
    "general_conversation": IntentDefinition(
        name="general_conversation",
        description="General conversation or question that needs no tool",
        requires_execution=False,
        example_phrases=["hi", "hello", "how are you", "thank you"],
    ),
    "check_process": IntentDefinition(
        name="check_process",
        description="Check if a process is running",
        example_phrases=["is chrome running", "check process"],
    ),
    "store_memory": IntentDefinition(
        name="store_memory",
        description="Store something in memory",
        example_phrases=["remember that", "note that", "save this"],
    ),
    "recall_memory": IntentDefinition(
        name="recall_memory",
        description="Recall something from memory",
        example_phrases=["remember when", "what did I say about", "recall"],
    ),
}


def get_intent(name: str) -> IntentDefinition | None:
    """Look up an intent definition by name."""
    return KNOWN_INTENTS.get(name)


def intent_requires_execution(intent: str) -> bool:
    """Return whether this intent requires a tool to be executed."""
    defn = KNOWN_INTENTS.get(intent)
    return defn.requires_execution if defn else True


def normalize_intent(raw: str) -> str:
    """
    Normalize a raw intent string to a canonical known intent name.
    
    Falls back to the raw value if not found.
    """
    raw_lower = raw.lower().strip().replace(" ", "_").replace("-", "_")
    if raw_lower in KNOWN_INTENTS:
        return raw_lower
    # Fuzzy match: check if raw is a substring of a known intent
    for known in KNOWN_INTENTS:
        if raw_lower in known or known in raw_lower:
            return known
    return raw_lower
