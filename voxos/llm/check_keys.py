"""Check every configured Groq key with one tiny request.

    python -m voxos.llm.check_keys

Prints each key's position, whether it works and its remaining quota. Keys
themselves are never printed.
"""

from __future__ import annotations

import asyncio

from openai import AuthenticationError, RateLimitError

from voxos.config import get_settings
from voxos.llm.base import LLMConfig, LLMMessage
from voxos.llm.openai_compat import OpenAICompatibleProvider


async def main() -> int:
    settings = get_settings().llm
    keys = settings.all_groq_keys()
    if not keys:
        print("No Groq keys configured. Set GROQ_API_KEYS=key1,key2,key3 in .env")
        return 1

    config = LLMConfig(model=settings.fast_model, max_tokens=8, temperature=0)
    ok = 0
    for i, key in enumerate(keys):
        provider = OpenAICompatibleProvider(
            settings.base_url, key, settings.provider, retry_rate_limits=False
        )
        try:
            await provider.complete([LLMMessage(role="user", content="ping")], config)
            state = provider.rate_limit_state
            print(
                f"key {i} (...{key[-4:]}): OK  "
                f"tokens/min left={state.get('remaining_tokens', '?')}  "
                f"requests/day left={state.get('remaining_requests', '?')}"
            )
            ok += 1
        except AuthenticationError:
            print(f"key {i} (...{key[-4:]}): REJECTED (invalid key)")
        except RateLimitError:
            print(f"key {i} (...{key[-4:]}): RATE LIMITED right now (key is valid)")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"key {i} (...{key[-4:]}): ERROR {type(e).__name__}: {e}")
    print(f"{ok}/{len(keys)} keys usable")
    return 0 if ok == len(keys) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
