"""
browser.media — pause, play, stop, mute or skip whatever is playing.

Works on any site's <video>/<audio> elements, not just YouTube. With no tab
given it picks the tab that fits the action: pause/stop/mute act on the tab
that is playing, play/unmute on the paused one. When Vox's browser has no
media at all (music in Spotify or another app), it falls back to the
system media keys.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolMetadata, ToolResult, VerificationType

log = get_logger(__name__)

MediaAction = Literal["pause", "play", "toggle", "stop", "mute", "unmute", "next"]

_ACTION_JS = {
    "pause": "() => { document.querySelectorAll('video, audio').forEach(m => m.pause()); }",
    "stop": "() => { document.querySelectorAll('video, audio').forEach(m => m.pause()); }",
    "play": "() => { const m = document.querySelector('video, audio'); if (m) return m.play(); }",
    "toggle": (
        "() => { const m = document.querySelector('video, audio');"
        " if (m) { if (m.paused) return m.play(); m.pause(); } }"
    ),
    "mute": "() => { document.querySelectorAll('video, audio').forEach(m => { m.muted = true; }); }",
    "unmute": "() => { document.querySelectorAll('video, audio').forEach(m => { m.muted = false; }); }",
}

# Which tabs an action can apply to, best first.
_WANTS = {
    "pause": ("playing",),
    "stop": ("playing",),
    "mute": ("playing",),
    "next": ("playing", "paused"),
    "toggle": ("playing", "paused"),
    "play": ("paused", "playing"),
    "unmute": ("playing", "paused"),
}

# Without browser media we cannot see whether something plays, so pause and
# stop send the Stop key: Play/Pause would START music that was not playing.
_SYSTEM_KEY = {
    "pause": "stop", "stop": "stop", "play": "playpause", "toggle": "playpause",
    "next": "nexttrack", "mute": "volumemute", "unmute": "volumemute",
}


class BrowserMediaArgs(BaseModel):
    action: MediaAction = Field(..., description="pause, play, toggle, stop, mute, unmute or next")
    tab: str | None = Field(default=None, description="Tab id from the tab list; omit to pick automatically")


_MEDIA_SITES = (
    "youtube.com", "youtu.be", "music.youtube", "spotify.com", "soundcloud.com", "jiosaavn.com",
    "gaana.com", "wynk.in", "music.apple.com", "music.amazon", "netflix.com", "primevideo.com",
    "hotstar.com", "twitch.tv", "vimeo.com",
)

# The tab the last media command acted on: "resume" should continue what
# "pause" stopped, not whatever else has a video on the page.
_last_media_tab: str | None = None


def _is_media_site(tab: dict[str, Any]) -> bool:
    return any(site in tab.get("url", "") for site in _MEDIA_SITES)


def choose_tab(
    state: list[dict[str, Any]], action: str, last_media_tab: str | None = None
) -> dict[str, Any] | None:
    """The tab an action should apply to.

    Preference: the tab Vox last controlled, then media sites, then the
    most recently used. A paused video on a shop or news page is only
    resumed when Vox paused it: Amazon's product video is not "the music".
    """
    for wanted in _WANTS[action]:
        matches = [t for t in state if t["media"] == wanted]
        if wanted == "paused":
            matches = [
                t for t in matches
                if t["tab"] == last_media_tab or _is_media_site(t) or not t.get("url")
            ]
        if matches:
            return max(matches, key=lambda t: (
                t["tab"] == last_media_tab, _is_media_site(t), bool(t.get("active")),
            ))
    return None


class BrowserMediaTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.media",
            description=(
                "Pause, play, stop, mute, unmute or skip (next) the video or music that is playing "
                "(any website; falls back to the system media keys for other apps)."
            ),
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return BrowserMediaArgs

    async def _execute(self, args: BrowserMediaArgs) -> ToolResult:
        global _last_media_tab
        from voxos.tools.browser import tools as browser

        try:
            state = await browser.browser_tabs_state()
        except Exception as e:  # browser never started
            log.info("browser_media_no_browser", error=str(e))
            state = []

        if args.tab:
            target = next((t for t in state if t["tab"] == args.tab), None)
        else:
            target = choose_tab(state, args.action, _last_media_tab)

        if target is None or target["media"] in ("none", "unknown"):
            return self._system_media_key(args.action)

        page = browser.page_for_tab(target["tab"])
        if page is None:
            return ToolResult.fail(f"Tab {target['tab']} is closed.", "NOT_FOUND")
        try:
            if args.action == "next":
                if "youtube.com" not in page.url:
                    return ToolResult.fail("Skipping to the next track only works on YouTube.", "NOT_SUPPORTED")
                await page.keyboard.press("Shift+N")
            else:
                await page.evaluate(_ACTION_JS[args.action])
            after = await page.evaluate(browser._MEDIA_STATE_JS)
        except Exception as e:
            return ToolResult.fail(error=str(e), error_code="BROWSER_ERROR")

        _last_media_tab = target["tab"]
        log.info("browser_media", action=args.action, tab=target["tab"], media=after)
        return ToolResult.ok(data={
            "action": args.action,
            "tab": target["tab"],
            "title": target["title"],
            "media": after,
        })

    @staticmethod
    def _system_media_key(action: str) -> ToolResult:
        try:
            import pyautogui
        except ImportError:
            return ToolResult.fail("Nothing is playing in Vox's browser.", "NOT_FOUND")
        pyautogui.press(_SYSTEM_KEY[action])
        log.info("system_media_key", action=action, key=_SYSTEM_KEY[action])
        return ToolResult.ok(data={
            "action": action,
            "tab": None,
            "title": "system media keys (another app)",
            "media": "unknown",
        })
