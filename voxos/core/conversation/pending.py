"""
Questions Vox asked and is waiting on, per session.

When a worker needs a choice or a go-ahead, the reply asks it and the
questions are kept here. The user's next message is then the answer: it
goes back to the agents together with the original request and the
questions, instead of to chat. Questions expire, so an answer given much
later is treated as a new request.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

TTL_S = 10 * 60


def _now() -> float:
    return time.monotonic()


@dataclass
class PendingQuestions:
    request: str
    questions: list[dict[str, Any]]
    asked_at: float = field(default_factory=lambda: _now())


_PENDING: dict[str, PendingQuestions] = {}


def remember(session_id: str, request: str, questions: list[dict[str, Any]]) -> None:
    if questions:
        _PENDING[session_id] = PendingQuestions(request=request, questions=questions)


def get(session_id: str) -> PendingQuestions | None:
    item = _PENDING.get(session_id)
    if item is not None and _now() - item.asked_at > TTL_S:
        _PENDING.pop(session_id, None)
        return None
    return item


def clear(session_id: str) -> None:
    _PENDING.pop(session_id, None)


_ANSWER = re.compile(r'The user answered: "(.*?)"\.', re.DOTALL)
_YES = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|ok|okay|sure|go ahead|proceed|confirm(?:ed)?|do it|place it|buy it|"
    r"send it|delete it|haan|ha|han|ji)\b",
    re.IGNORECASE,
)


_QUESTION = re.compile(
    r"^\s*(?:what|which|who|whom|whose|how|why|when|where|can|could|would|will|is|are|do|does|did|"
    r"should|tell me|show me|open|play|search|find|close)\b",
    re.IGNORECASE,
)
_SHORT_REPLY = 6


def looks_like_answer(reply: str, questions: list[dict[str, Any]]) -> bool:
    """Whether a message answers the pending questions or is a new request.

    "which laptop brand do I like?" arrived while a login question was
    pending and was treated as its answer, so Vox asked the same thing again.
    """
    text = reply.strip()
    lower = text.lower()
    if resolve_choice(text, questions) != text:
        return True  # "2", "the second one"
    options = [option_label(o).lower() for q in questions for o in q.get("options") or []]
    if any(o and (o in lower or lower in o) for o in options):
        return True
    if _YES.match(text) or re.match(r"^\s*(?:no|nope|nah|don'?t|do not|cancel|stop|skip)\b", lower):
        return True
    if text.endswith("?") or _QUESTION.match(text):
        return False
    return len(text.split()) <= _SHORT_REPLY


def user_said_yes(request: str) -> bool:
    """True when this request is the user's affirmative answer to a Vox question."""
    match = _ANSWER.search(request or "")
    return bool(match and _YES.match(match.group(1)))


def option_label(option: Any) -> str:
    """Options are plain strings, or {"label", "description"} dicts."""
    if isinstance(option, dict):
        return str(option.get("label") or "").strip()
    return str(option).strip()


_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "pehla": 1, "pehle": 1, "pahla": 1, "doosra": 2, "dusra": 2, "doosre": 2, "dusre": 2,
    "teesra": 3, "tisra": 3, "teesre": 3, "chautha": 4, "chauthe": 4,
}
_PICK = re.compile(
    r"^\s*(?:the\s+)?(?:option|number|no\.?|choice)?\s*#?\s*(\d|[a-z]+)(?:st|nd|rd|th)?"
    r"(?:\s+(?:one|option|wala|vala|waala))?\s*[.!]?\s*$",
    re.IGNORECASE,
)


def resolve_choice(answer: str, questions: list[dict[str, Any]]) -> str:
    """Turn "2", "the second one", "option two" or "doosra" into the option it names.

    Only for a single question with options; spoken answers are usually like
    this, and the agents otherwise had to guess what "the second one" meant.
    """
    if len(questions) != 1:
        return answer
    options = [option_label(o) for o in questions[0].get("options") or [] if option_label(o)]
    match = _PICK.match(answer or "")
    if not options or not match:
        return answer
    token = match.group(1).lower()
    if token == "last":
        return options[-1]
    n = int(token) if token.isdigit() else _ORDINALS.get(token)
    return options[n - 1] if n and 1 <= n <= len(options) else answer


def _asked(q: dict[str, Any]) -> str:
    options = [option_label(o) for o in q.get("options") or [] if option_label(o)]
    return f"{q['question']} (options: {', '.join(options)})" if options else q["question"]


def as_request(item: PendingQuestions, answer: str) -> str:
    """The request the agents continue with: original, what was asked, the reply."""
    asked = " ".join(
        f"{n}. {_asked(q)}" for n, q in enumerate(item.questions, 1)
    ) if len(item.questions) > 1 else _asked(item.questions[0])
    return (
        f"{item.request}\n"
        f"(Vox asked the user: {asked}\n"
        f"The user answered: \"{answer}\". Continue the request using this answer; "
        f"if the answer is a new, unrelated request, do that instead.)"
    )
