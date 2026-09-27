"""
Synthesizer — merges Worker results into one reply Vox can speak.
"""

from __future__ import annotations

import re

from voxos.llm.router import TaskComplexity
from voxos.observability.logger import get_logger
from voxos.orchestration.llm import JsonAsker
from voxos.orchestration.worker import WorkerResult

log = get_logger(__name__)

SYNTH_PROMPT = """You are Vox, a voice assistant. Several agents worked on parts of the user's
request. Write ONE short spoken reply (at most 4 sentences, no markdown, no lists, no URLs) that
gives the user the actual findings — the real numbers, names and headlines, not "I found it" — and
plainly says what failed and why. Never claim a failed part succeeded.
Use ONLY facts written in the results below. Never add names, prices, numbers or details that are
not there; if a result only says a page was opened, say that and nothing more.
Reply in the user's language ({language}).
Respond with JSON: {{"reply": "..."}}"""


_URL = re.compile(r"\(?\bhttps?://[^\s)]+\)?")
_MARKDOWN = re.compile(r"\*\*|__|`|^#+\s*", re.MULTILINE)
_DANGLING_DASH = re.compile(r"\s+[–—-]\s+(?=[–—-]|\.|,|$)")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:])")


def speakable(text: str) -> str:
    """Text Vox can say aloud: no URLs or markdown, whatever the model wrote."""
    text = _URL.sub("", text)
    text = _MARKDOWN.sub("", text)
    text = _DANGLING_DASH.sub(" ", text)  # the "– " a removed URL leaves behind
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _line(result: WorkerResult) -> str:
    if result.status == "done":
        return f"- {result.goal}: DONE. {result.answer}"
    if result.status == "skipped":
        return f"- {result.goal}: SKIPPED because an earlier part failed."
    reason = result.error or result.answer or "unknown error"
    blocked = " (blocked by safety policy)" if result.blocked else ""
    return f"- {result.goal}: FAILED{blocked}. {reason}"


def _fallback(results: list[WorkerResult]) -> str:
    parts = []
    for r in results:
        if r.status == "done":
            parts.append(r.answer or f"Done: {r.goal}.")
        elif r.status == "failed":
            parts.append(f"I couldn't do this: {r.goal} ({r.error or 'it failed'}).")
    return " ".join(parts) or "I couldn't complete that request."


async def synthesize(
    request: str, results: list[WorkerResult], llm: JsonAsker, language: str = "en"
) -> str:
    if len(results) == 1 and results[0].status == "done" and results[0].answer:
        return speakable(results[0].answer)
    try:
        data = await llm.ask(
            "synthesizer",
            SYNTH_PROMPT.format(language=language),
            f"User's request: {request}\n\nResults:\n" + "\n".join(_line(r) for r in results),
            complexity=TaskComplexity.SIMPLE,
            max_tokens=400,
        )
        reply = speakable(str(data.get("reply") or ""))
        if reply:
            return reply
    except Exception as e:  # noqa: BLE001
        log.warning("synthesizer_failed_using_fallback", error=str(e))
    return speakable(_fallback(results))
