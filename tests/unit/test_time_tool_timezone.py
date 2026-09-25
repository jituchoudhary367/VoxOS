"""`system.get_time` answers with the clock on the user's wall, not UTC.

VoxOS runs on the user's own desktop, so "what time is it" means local time.
Defaulting to UTC made the assistant 5h30m wrong in India.
"""

from __future__ import annotations

from datetime import datetime

import pytest


@pytest.mark.asyncio
async def test_time_defaults_to_the_machines_local_timezone() -> None:
    from voxos.tools.system.tools import GetCurrentTimeTool

    result = await GetCurrentTimeTool().execute({})

    assert result.success
    returned = datetime.fromisoformat(result.data["datetime"])
    assert returned.utcoffset() == datetime.now().astimezone().utcoffset()


@pytest.mark.asyncio
async def test_local_time_matches_the_current_wall_clock() -> None:
    from voxos.tools.system.tools import GetCurrentTimeTool

    result = await GetCurrentTimeTool().execute({})

    returned = datetime.fromisoformat(result.data["datetime"])
    drift = abs((returned - datetime.now().astimezone()).total_seconds())
    assert drift < 10
    assert result.data["hour"] == returned.hour


@pytest.mark.asyncio
async def test_an_explicitly_requested_timezone_is_still_honoured() -> None:
    from voxos.tools.system.tools import GetCurrentTimeTool

    result = await GetCurrentTimeTool().execute({"timezone_name": "UTC"})

    assert result.success
    assert datetime.fromisoformat(result.data["datetime"]).utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_an_unknown_timezone_falls_back_to_local_time() -> None:
    from voxos.tools.system.tools import GetCurrentTimeTool

    result = await GetCurrentTimeTool().execute({"timezone_name": "Mars/Olympus_Mons"})

    assert result.success
    returned = datetime.fromisoformat(result.data["datetime"])
    assert returned.utcoffset() == datetime.now().astimezone().utcoffset()
