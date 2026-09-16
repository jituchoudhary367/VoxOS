"""Tests that the desktop UI receives presentation text, not task payloads."""

import pytest


def test_ui_reply_text_never_serializes_the_reply_object() -> None:
    from voxos.core.conversation.models import AssistantReply
    from voxos.ui.app import reply_text

    reply = AssistantReply(text="Opened Notepad.", kind="success", action="open")

    assert reply_text(reply) == "Opened Notepad."
    assert "{" not in reply_text(reply)


@pytest.mark.asyncio
async def test_model_configuration_failure_does_not_trigger_local_fallback() -> None:
    from voxos.llm.base import LLMConfig, LLMMessage
    from voxos.llm.router import FallbackProvider

    class Primary:
        name = "primary"

        async def complete(self, *_args):
            raise ValueError("model not found")

    class Fallback:
        name = "fallback"
        was_called = False

        async def complete(self, *_args):
            self.was_called = True

    fallback = Fallback()
    provider = FallbackProvider(Primary(), fallback)

    with pytest.raises(ValueError, match="model not found"):
        await provider.complete(
            [LLMMessage(role="user", content="hi")],
            LLMConfig(model="missing"),
        )

    assert fallback.was_called is False
