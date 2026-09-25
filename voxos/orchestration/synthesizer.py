"""
Synthesizer — merges Worker results into one reply Vox can speak.
"""

from __future__ import annotations

from voxos.llm.router import TaskComplexity
from voxos.observability.logger import get_logger
from voxos.orchestration.llm import JsonAsker
from voxos.orchestration.worker import WorkerResult

log = get_logger(__name__)

SYNTH_PROMPT = """You are Vox, a voice assistant. Several agents worked on parts of the user's
request. Write ONE short spoken reply (at most 4 sentences, no markdown, no lists) that gives the
user the actual findings — the real numbers, names and headlines, not "I found it" — and plainly
says what failed and why. Never claim a failed part succeeded.
Reply in the user's language ({language}).
Respond with JSON: {{"reply": "..."}}"""


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
        return results[0].answer
    try:
        data = await llm.ask(
            "synthesizer",
            SYNTH_PROMPT.format(language=language),
            f"User's request: {request}\n\nResults:\n" + "\n".join(_line(r) for r in results),
            complexity=TaskComplexity.SIMPLE,
            max_tokens=400,
        )
        reply = str(data.get("reply") or "").strip()
        if reply:
            return reply
    except Exception as e:  # noqa: BLE001
        log.warning("synthesizer_failed_using_fallback", error=str(e))
    return _fallback(results)
