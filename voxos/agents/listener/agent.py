"""
Listener Agent — "What does the user want?"

Responsibilities:
- Understand the user's request
- Detect intent and language
- Extract entities
- Normalize to language-independent internal representation
- Handle ambiguous or multi-part requests
- NEVER execute tools

Input: raw text or voice transcript
Output: structured intent + entities + language
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from voxos.agents.base import BaseAgent
from voxos.core.events.bus import VoxEvent, get_event_bus
from voxos.core.events.types import EventType
from voxos.core.router import is_follow_up
from voxos.llm.base import LLMMessage
from voxos.llm.router import TaskComplexity, get_llm_router
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()


# ─────────────────────────────────────────────────────────────────────────────
# I/O schemas
# ─────────────────────────────────────────────────────────────────────────────

class ListenerInput(BaseModel):
    text: str
    session_id: str | None = None
    language: str = "auto"
    conversation_history: list[dict[str, str]] = Field(default_factory=list)


class ListenerOutput(BaseModel):
    intent: str
    language: str = "en"
    entities: dict[str, Any] = Field(default_factory=dict)
    requires_execution: bool = True
    confidence: float = 1.0
    clarification_needed: bool = False
    clarification_question: str | None = None
    raw_text: str = ""
    # "rules" when the deterministic classifier matched; "llm" otherwise. The
    # router trusts rule matches for the fast path but checks LLM guesses.
    source: str = "llm"


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Listener Agent for VoxOS, an autonomous AI assistant.

Your ONLY job is to understand the user's request and extract:
1. INTENT — what the user wants to accomplish (language-independent)
2. LANGUAGE — detected language code (e.g. "en", "hi", "auto")
3. ENTITIES — key named entities in the request
4. REQUIRES_EXECUTION — does this need a tool or can it be answered directly?

IMPORTANT: You must respond ONLY with a valid JSON object. No explanation, no markdown.

Intent naming rules:
- Use snake_case
- Be specific: "open_application", "search_web", "get_system_info", "get_current_time"
- Multilingual equivalents map to the SAME intent:
  "Open Chrome" = "Chrome kholo" = "Chrome खोलो" → intent: "open_application", entity: {application: "chrome"}
- IMPORTANT: If a user says "open chrome and search..." or asks to play a video/music, the primary intent is "search_web" or "play_media", NOT "open_application".

Example response:
{
  "intent": "get_current_time",
  "language": "en",
  "entities": {"timezone": "IST"},
  "requires_execution": true,
  "confidence": 0.95,
  "clarification_needed": false
}

Common intents:
- get_current_time, get_system_info, get_resources
- open_application, close_application, list_applications
- search_web, browse_url
- list_files, read_file, create_file, delete_file, move_file
- run_command, check_process
- take_screenshot
- general_conversation (for greetings, questions that need no tool)
- research_topic (complex multi-step research)
- create_document, summarize_document
- check_email, send_email (requires explicit user authorization)
"""

# A second action after the first ("... and also tell me ...").
_MORE_ACTIONS = re.compile(r"\s(?:and|then|also|after that|afterwards|plus)\s")

_ENGINES = ("google", "youtube", "bing", "wikipedia")
_ENGINE_FIRST = re.compile(rf"^(?:on\s+)?({'|'.join(_ENGINES)})\s+(?:for\s+)?(.+)$")
_ENGINE_LAST = re.compile(rf"^(.+?)\s+(?:on|in|using)\s+({'|'.join(_ENGINES)})$")
_THE_WEB = re.compile(r"^(?:the\s+)?(?:web|internet|net|online)\s+(?:for\s+)?(.+)$")


# Media commands whose meaning is certain. "play that" / "play it" are not
# here: they name something from earlier, which only the AI can resolve.
_MEDIA_THING = r"(?:the\s+)?(?:music|song|video|track|audio|playback|sound)"
_MEDIA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("unmute", re.compile(rf"^unmute(?:\s+(?:it|{_MEDIA_THING}))?$")),
    ("mute", re.compile(rf"^mute(?:\s+(?:it|{_MEDIA_THING}))?$")),
    ("next", re.compile(
        rf"^(?:(?:play\s+)?next\s+(?:song|track|video)|skip(?:\s+(?:this|the))?(?:\s+(?:song|track|video))?)$"
    )),
    ("pause", re.compile(rf"^pause(?:\s+(?:it|{_MEDIA_THING}))?$")),
    ("stop", re.compile(rf"^stop\s+(?:it|playing|{_MEDIA_THING})$")),
    ("play", re.compile(
        rf"^(?:play|play again|unpause|resume(?:\s+{_MEDIA_THING})?|continue(?:\s+playing)?)$"
    )),
)
_POLITE = re.compile(r"^(?:please|vox|hey vox)\s+|\s+(?:please|now)$")


def _media_action(text_clean: str) -> str | None:
    text = _POLITE.sub("", text_clean.strip()).strip()
    for action, pattern in _MEDIA_PATTERNS:
        if pattern.match(text):
            return action
    return None


def _search_target(text: str) -> tuple[str, str]:
    """Split "google for best laptops" into ("best laptops", "google")."""
    text = text.strip()
    if text in _ENGINES:  # "search google" = go to Google
        return text, text
    if match := _ENGINE_FIRST.match(text):
        return match.group(2).strip(), match.group(1)
    if match := _ENGINE_LAST.match(text):
        return match.group(1).strip(), match.group(2)
    if match := _THE_WEB.match(text):
        return match.group(1).strip(), "bing"
    return text, "bing"


class ListenerAgent(BaseAgent[ListenerInput, ListenerOutput]):
    """
    Understands user intent from voice transcripts or text.
    Never executes tools — only classifies and extracts.
    """

    def __init__(self) -> None:
        self._router = get_llm_router()
        self._bus = get_event_bus()

    @property
    def name(self) -> str:
        return "listener"

    async def process(self, input: ListenerInput) -> ListenerOutput:
        """Classify user intent and extract entities."""
        log.info("listener_processing", session_id=input.session_id, text_length=len(input.text))

        async with metrics.async_timer("agent.latency", agent=self.name):
            output = await self._classify_intent(input)

        # Emit event
        await self._bus.publish(VoxEvent(
            event_type=EventType.INTENT_DETECTED,
            payload={
                "intent": output.intent,
                "language": output.language,
                "entities": output.entities,
                "confidence": output.confidence,
            },
            session_id=input.session_id,
            agent=self.name,
        ))

        metrics.increment("listener.intents", intent=output.intent)
        log.info(
            "listener_output",
            intent=output.intent,
            language=output.language,
            requires_execution=output.requires_execution,
            confidence=output.confidence,
        )
        return output

    async def _ask_json(self, messages: list[LLMMessage], config: Any) -> dict[str, Any] | None:
        """The classification JSON; None when Groq keeps failing its JSON check.

        gpt-oss intermittently fails Groq's JSON validation (400
        json_validate_failed / tool_use_failed); that crashed the whole turn.
        """
        from openai import BadRequestError

        from voxos.orchestration.llm import _is_json_generation_error, recover_failed_generation

        for attempt in range(2):
            try:
                response = await self._router.provider.complete(messages, config)
                return json.loads(response.content)
            except BadRequestError as e:
                recovered = recover_failed_generation(e)
                if recovered is not None:
                    return recovered
                if not _is_json_generation_error(e):
                    raise
                log.warning("listener_json_generation_failed", attempt=attempt + 1)
        return None

    async def _classify_intent(self, input: ListenerInput) -> ListenerOutput:
        """Use LLM to classify intent (or fast-path for obvious patterns)."""
        # Fast path: deterministic patterns that don't need LLM
        fast_result = self._fast_classify(input.text)
        if fast_result:
            log.debug("listener_fast_path", intent=fast_result.intent)
            return fast_result

        # LLM classification
        messages = [
            LLMMessage(role="user", content=input.text),
        ]
        # Add recent conversation context
        for turn in input.conversation_history[-4:]:  # Last 2 turns
            messages.insert(-1, LLMMessage(role=turn["role"], content=turn["content"]))

        config = self._router.config_for(
            complexity=TaskComplexity.SIMPLE,
            response_format={"type": "json_object"},
            system_prompt=SYSTEM_PROMPT,
            temperature=0.0,  # Deterministic classification
            # Reasoning models (gpt-oss) spend ~500 hidden tokens before the
            # JSON; a 256 cap truncates to empty and Groq rejects the call.
            max_tokens=1024,
        )

        try:
            data = await self._ask_json(messages, config)
            if data is None:
                # Groq failed to produce JSON twice. Guessing "chat" would
                # drop a command; the agents can both act and just answer.
                log.warning("listener_llm_unavailable_routing_to_agents")
                return ListenerOutput(
                    intent="unknown", language="en", entities={}, requires_execution=True,
                    confidence=0.3, raw_text=input.text,
                )
            return ListenerOutput(
                intent=data.get("intent", "general_conversation"),
                language=data.get("language", "en"),
                entities=data.get("entities", {}),
                requires_execution=data.get("requires_execution", True),
                confidence=float(data.get("confidence", 0.9)),
                clarification_needed=data.get("clarification_needed", False),
                clarification_question=data.get("clarification_question"),
                raw_text=input.text,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.warning("listener_llm_parse_error", error=str(e), content=response.content[:200])
            # Fallback: treat as general conversation
            return ListenerOutput(
                intent="general_conversation",
                language="en",
                entities={},
                requires_execution=False,
                confidence=0.5,
                raw_text=input.text,
            )

    @staticmethod
    def _fast_classify(text: str) -> ListenerOutput | None:
        """
        Deterministic fast-path for common patterns.
        Avoids an LLM call for simple, obvious requests (Class A intents).
        """
        result = ListenerAgent._rule_classify(text)
        if result is not None:
            result.source = "rules"
            result.raw_text = text
        return result

    @staticmethod
    def _rule_classify(text: str) -> ListenerOutput | None:
        import re

        text_lower = text.lower().strip()

        text_clean = re.sub(r'[^\w\s]', '', text_lower)  # strip punctuation
        # Paths and domains need their punctuation. `text_raw` keeps case for
        # captured values; `text_lp` is the same string lower-cased, so match
        # spans line up between the two.
        text_raw = text.strip().rstrip("?!.").strip()
        text_lp = text_raw.lower()

        def raw_group(match: re.Match, group: int) -> str:
            return text_raw[match.start(group):match.end(group)].strip().strip("'\"")

        # ── Deterministic browser actions ────────────────────────────────────
        if text_clean in {"open browser", "open the browser", "launch browser"}:
            return ListenerOutput(intent="open_browser", language="en",
                                  entities={"url": "about:blank"},
                                  requires_execution=True, confidence=1.0)

        if text_clean in {"open youtube", "open the youtube", "launch youtube"}:
            return ListenerOutput(intent="open_youtube", language="en",
                                  entities={"url": "https://www.youtube.com"},
                                  requires_execution=True, confidence=1.0)

        browser_search = re.match(
            r"^(?:(?:open (?:chrome|browser) and )|now )?search(?: for)?\s+(.+)$",
            text_clean,
        )
        # Only a search and nothing else. "search the web for X and also tell
        # me my CPU" is two jobs; typing the whole sentence into Bing was the
        # bug, so anything with a further action goes to the AI instead.
        # A search that refers back ("search more ... from another company",
        # "search that on amazon") has to be resolved with the conversation
        # and the open tabs, which only the AI sees.
        if (
            browser_search
            and not _MORE_ACTIONS.search(f" {browser_search.group(1)} ")
            and not is_follow_up(browser_search.group(1))
        ):
            query, engine = _search_target(browser_search.group(1).strip())
            if query:
                return ListenerOutput(
                    intent="search_web_browser",
                    language="en",
                    entities={"query": query, "engine": engine},
                    requires_execution=True,
                    confidence=1.0,
                )

        # If it's a complex multi-part request, bypass fast path for Strategist routing
        complex_keywords = [" and ", " then ", " also ", " after ", " search ", " play ", " summarize ", " find "]
        if any(kw in text_lower for kw in complex_keywords):
            return None

        # ── Media: pause / play / stop / skip / mute what is playing ─────────
        media_action = _media_action(text_clean)
        if media_action:
            return ListenerOutput(intent="media_control", language="en",
                                  entities={"action": media_action},
                                  requires_execution=True, confidence=1.0)

        # ── Greetings ─────────────────────────────────────────────────────────
        GREETINGS = {"hi", "hello", "hey", "hey voxos", "hi voxos", "hello voxos",
                     "namaste", "namaskar", "hey vox"}
        if text_clean in GREETINGS:
            return ListenerOutput(intent="general_conversation", language="en",
                                  entities={"type": "greeting"},
                                  requires_execution=False, confidence=1.0)

        # ── Time / Date ───────────────────────────────────────────────────────
        TIME_PATTERNS = [
            r"what(?:'s| is) (?:the )?(?:current )?time",
            r"^(?:current )?time$", r"samay kya hai", r"time bolo", r"kitne baje",
        ]
        if any(re.search(p, text_clean) for p in TIME_PATTERNS):
            lang = "hi" if any(w in text_clean for w in ["kya", "bolo", "hai", "baje"]) else "en"
            return ListenerOutput(intent="get_current_time", language=lang,
                                  entities={}, requires_execution=True, confidence=1.0)

        DATE_PATTERNS = [r"what(?:'s| is) (?:the )?(?:today's )?date", r"^date$", r"today(?:'s)? date"]
        if any(re.search(p, text_clean) for p in DATE_PATTERNS):
            return ListenerOutput(intent="get_current_time", language="en",
                                  entities={}, requires_execution=True, confidence=1.0)

        # ── System Resources ──────────────────────────────────────────────────
        SYS_RES_PATTERNS = [
            r"(?:how much|check|show|what(?:'s| is)(?: my)?)\s+(?:ram|memory|cpu|processor|disk|storage|battery)",
            r"(?:cpu|ram|memory|disk|battery|network) usage",
            r"system (?:resources|stats|status|metrics)",
            r"ram (?:kitna|check|batao)", r"cpu (?:kitna|check|batao)",
        ]
        if any(re.search(p, text_clean) for p in SYS_RES_PATTERNS):
            return ListenerOutput(intent="get_resources", language="en",
                                  entities={}, requires_execution=True, confidence=1.0)

        # ── System Info ───────────────────────────────────────────────────────
        # Word boundaries matter: without them "close", "choose" and any path
        # containing "os" were classified as system-info requests.
        SYS_INFO_PATTERNS = [
            r"\b(?:os|operating system|hostname|computer name|uptime|system info(?:rmation)?)\b",
            r"\babout (?:this )?(?:computer|system|pc|laptop)\b",
        ]
        if any(re.search(p, text_clean) for p in SYS_INFO_PATTERNS):
            return ListenerOutput(intent="get_system_info", language="en",
                                  entities={}, requires_execution=True, confidence=1.0)

        # ── Recent Files / Downloads ──────────────────────────────────────────
        RECENT_DOWNLOADS_REGEX = re.compile(
            r"^(?:show|find|search|get) (?:last|past|recent) (?:(?:(\d+)|(one|two|three|four|five|six|seven|ten)) days )?(?:downloads|files in downloads)$"
        )
        m = RECENT_DOWNLOADS_REGEX.match(text_clean)
        if m:
            days_str = m.group(1) or m.group(2)
            word_to_num = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10}
            days = int(days_str) if days_str and days_str.isdigit() else word_to_num.get(days_str, 2)
            return ListenerOutput(intent="search_files", language="en",
                                  entities={"search_path": "downloads", "modified_within_days": days},
                                  requires_execution=True, confidence=1.0)

        # ── Screenshot ────────────────────────────────────────────────────────
        SCREENSHOT_PATTERNS = [
            r"^screenshot$", r"take (?:a )?screenshot", r"capture (?:the )?screen",
            r"screenshot (?:le|lo|karo)",
        ]
        if any(re.search(p, text_clean) for p in SCREENSHOT_PATTERNS):
            return ListenerOutput(intent="take_screenshot", language="en",
                                  entities={}, requires_execution=True, confidence=1.0)

        # ── Open a website ────────────────────────────────────────────────────
        # Before the app launcher, which would otherwise try to launch an
        # application called "github.com in the browser".
        m = re.match(
            r"^(?:please\s+)?(?:open|go to|visit|browse to|navigate to)\s+(?:the\s+)?(?:website\s+|site\s+)?"
            r"(https?://\S+|(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.([a-z]{2,24})(?:/\S*)?)"
            r"(?:\s+(?:in|on|using|with)\s+(?:the\s+|my\s+)?(?:browser|chrome|edge|firefox|web browser))?$",
            text_lp,
        )
        FILE_EXTENSIONS = {"txt", "py", "md", "csv", "pdf", "doc", "docx", "xls", "xlsx", "json", "log",
                           "exe", "png", "jpg", "jpeg", "gif", "zip", "html", "js", "ts", "ppt", "pptx", "ini"}
        if m and (m.group(1).startswith("http") or m.group(2) not in FILE_EXTENSIONS):
            url = raw_group(m, 1)
            if not url.lower().startswith(("http://", "https://")):
                url = f"https://{url}"
            return ListenerOutput(intent="open_browser", language="en",
                                  entities={"url": url},
                                  requires_execution=True, confidence=0.95)

        # ── Open Application ──────────────────────────────────────────────────
        OPEN_APP_REGEX = re.compile(
            r"^(?:please\s+)?(?:can you\s+)?(?:open|launch|start|run|kholo|chalu karo|shuru karo|start karo)\s+"
            r"(?:the\s+)?(.+?)(?:\s+(?:app|application|program|software|window))?(?:\s+(?:for me|please))?$"
        )
        m = OPEN_APP_REGEX.match(text_clean)
        if m:
            app_name = m.group(1).strip()
            STOPWORDS = {"the", "a", "an", "my", "it", "this", "that"}
            if app_name and app_name not in STOPWORDS and len(app_name) > 1:
                return ListenerOutput(intent="open_application", language="en",
                                      entities={"name": app_name},
                                      requires_execution=True, confidence=0.95)

        # ── Close Application ─────────────────────────────────────────────────
        CLOSE_APP_REGEX = re.compile(
            r"^(?:please\s+)?(?:can you\s+)?(?:close|quit|exit|kill|terminate|band karo)\s+"
            r"(?:the\s+)?(.+?)(?:\s+(?:app|application|program|window))?(?:\s+(?:for me|please))?$"
        )
        m = CLOSE_APP_REGEX.match(text_clean)
        if m:
            app_name = m.group(1).strip()
            if app_name and len(app_name) > 1:
                return ListenerOutput(intent="close_application", language="en",
                                      entities={"window_title": app_name},
                                      requires_execution=True, confidence=0.95)

        # ── Window Management ─────────────────────────────────────────────────
        m = re.match(r"^(?:focus|switch to|bring up|activate)\s+(.+?)(?:\s+window)?$", text_clean)
        if m:
            return ListenerOutput(intent="focus_window", language="en",
                                  entities={"window_title": m.group(1).strip()},
                                  requires_execution=True, confidence=0.90)

        m = re.match(r"^minimize\s+(.+?)(?:\s+window)?$", text_clean)
        if m:
            return ListenerOutput(intent="minimize_window", language="en",
                                  entities={"window_title": m.group(1).strip()},
                                  requires_execution=True, confidence=0.95)

        m = re.match(r"^maximize\s+(.+?)(?:\s+window)?$", text_clean)
        if m:
            return ListenerOutput(intent="maximize_window", language="en",
                                  entities={"window_title": m.group(1).strip()},
                                  requires_execution=True, confidence=0.95)

        # ── List Running Apps / Windows ───────────────────────────────────────
        if re.search(r"(?:what(?:'s| is)|list|show)(?: all)? (?:open |running )?(?:windows|apps|applications|programs)", text_clean):
            return ListenerOutput(intent="list_apps", language="en",
                                  entities={}, requires_execution=True, confidence=1.0)

        # ── Filesystem — List Directory ───────────────────────────────────────
        LIST_FILE_PATTERNS = [
            (r"(?:list|show)(?: all)? files?(?: on| in| inside| from)? (?:my )?desktop", "Desktop"),
            (r"(?:list|show)(?: all)? files?(?: on| in| inside| from)? (?:my )?documents", "Documents"),
            (r"(?:list|show)(?: all)? files?(?: on| in| inside| from)? (?:my )?downloads", "Downloads"),
        ]
        for pattern, folder in LIST_FILE_PATTERNS:
            if re.search(pattern, text_clean):
                return ListenerOutput(intent="list_files", language="en",
                                      entities={"dir_path": folder},
                                      requires_execution=True, confidence=0.95)

        m = re.search(r"(?:list|show)(?: all)? files?(?:\s+in|\s+inside|\s+from|\s+at)\s+(.+)", text_lp)
        if m:
            return ListenerOutput(intent="list_files", language="en",
                                  entities={"dir_path": raw_group(m, 1)},
                                  requires_execution=True, confidence=0.85)

        # ── Filesystem — Create Folder ────────────────────────────────────────
        m = re.search(
            r"(?:create|make|mkdir)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory|dir)\s+(?:called\s+|named\s+)?['\"]?([^'\"\s]+)['\"]?"
            r"(?:\s+(?:on|in|at|inside|under)\s+(.+))?$",
            text_lp,
        )
        if m:
            folder_name = raw_group(m, 1)
            location = raw_group(m, 2) if m.group(2) else "Desktop"
            if "/" in folder_name or "\\" in folder_name:
                path = folder_name
            else:
                sep = "\\" if "\\" in location else "/"
                path = f"{location.rstrip(chr(92) + '/')}{sep}{folder_name}"
            return ListenerOutput(intent="create_folder", language="en",
                                  entities={"dir_path": path},
                                  requires_execution=True, confidence=0.90)

        # ── Filesystem — Search Files ─────────────────────────────────────────
        m = re.search(
            r"^(?:please\s+)?find\s+(?:all\s+)?(?:the\s+|my\s+)?(\*?\.?[\w]+)\s+files?"
            r"(?:\s+(?:in|inside|on|from|under)\s+(.+))?$",
            text_lp,
        )
        if m:
            WORD_TO_EXT = {"python": "py", "text": "txt", "markdown": "md", "excel": "xlsx",
                           "word": "docx", "powerpoint": "pptx", "image": "png", "javascript": "js"}
            hint = m.group(1).lstrip("*.")
            ext = WORD_TO_EXT.get(hint, hint)
            location = raw_group(m, 2) if m.group(2) else "home"
            return ListenerOutput(intent="search_files", language="en",
                                  entities={"search_path": location, "pattern": f"*.{ext}"},
                                  requires_execution=True, confidence=0.85)

        # ── Clipboard ─────────────────────────────────────────────────────────
        if re.search(r"(?:what(?:'s| is) in(?: my)?|read|get|show) clipboard", text_clean):
            return ListenerOutput(intent="read_clipboard", language="en",
                                  entities={}, requires_execution=True, confidence=1.0)

        # ── Cancel / Stop ─────────────────────────────────────────────────────
        CANCEL_PHRASES = {"stop", "cancel", "never mind", "nevermind", "abort",
                          "stop that", "cancel that", "ruk ja", "band kar"}
        if text_clean in CANCEL_PHRASES or re.match(r"^(?:stop|cancel|abort)[\s!.]*$", text_clean):
            return ListenerOutput(intent="cancel_task", language="en",
                                  entities={}, requires_execution=False, confidence=1.0)

        return None
