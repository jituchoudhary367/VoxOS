"""
browser.fill_credential — type a saved username or password into the page.

The agent names the credential ("amazon") and the field; this tool puts
the secret into the page and returns only that it did. It refuses unless
  1. the user's own request asks to log in / sign in (a web page cannot
     talk the agent into logging somewhere), and
  2. the current tab is https on one of the credential's domains (a
     look-alike site never receives the password).
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from voxos.core.request_context import current_user_request
from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolMetadata, ToolResult, VerificationType
from voxos.vault.store import get_vault

log = get_logger(__name__)

LOGIN_INTENT = re.compile(r"\b(?:log\s*-?\s*in|login|sign\s*-?\s*in|signin|sign me in|authenticate)\b", re.IGNORECASE)

_FIELD_SELECTORS = {
    "password": ("input[type=password]",),
    "username": (
        "input[autocomplete=username]",
        "input[type=email]",
        "input[name*=email i]",
        "input[name*=user i]",
        "input[name*=login i]",
        "input[id*=email i]",
        "input[id*=user i]",
        "input[type=tel]",
        "input[type=text]",
    ),
}


class FillCredentialArgs(BaseModel):
    credential: str = Field(..., description="Name of a saved credential (from the list you were given)")
    field: Literal["username", "password"] = Field(..., description="Which field to fill")
    selector: str | None = Field(default=None, description="CSS selector of the field; omit to find it")


class FillCredentialTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browser.fill_credential",
            description=(
                "Fill the username or password field of a login form from a saved credential. "
                "You never see the value. Only works when the user asked to log in and the page "
                "is on the credential's own website."
            ),
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=20,
            category="browser",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return FillCredentialArgs

    async def _execute(self, args: FillCredentialArgs) -> ToolResult:
        from voxos.tools.browser import tools as browser

        if not LOGIN_INTENT.search(current_user_request()):
            log.warning("credential_refused_no_login_request", credential=args.credential)
            return ToolResult.fail(
                "The user did not ask to log in, so saved credentials were not used.",
                "LOGIN_NOT_REQUESTED",
            )

        vault = get_vault()
        if not any(c["name"] == args.credential.strip().lower() for c in vault.list()):
            return ToolResult.fail(f"No saved credential named '{args.credential}'.", "NOT_FOUND")

        page = await browser._get_browser_session()
        if not vault.allowed_on(args.credential, page.url):
            host = urlparse(page.url).hostname or page.url
            log.warning("credential_refused_wrong_site", credential=args.credential, site=host)
            return ToolResult.fail(
                f"This page ({host}) is not a site the '{args.credential}' login belongs to.",
                "WRONG_SITE",
            )

        value = vault.secret(args.credential, args.field)
        if not value:
            return ToolResult.fail(f"The saved '{args.credential}' login has no {args.field}.", "NOT_FOUND")

        selectors = (args.selector,) if args.selector else _FIELD_SELECTORS[args.field]
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    await locator.fill(value)
                    host = urlparse(page.url).hostname
                    log.info("credential_filled", credential=args.credential, field=args.field, site=host)
                    return ToolResult.ok(data={
                        "credential": args.credential, "field": args.field, "filled": True, "site": host,
                    })
            except Exception as e:  # noqa: BLE001 — try the next selector; never echo the value
                log.debug("credential_selector_failed", selector=selector, error=type(e).__name__)
        return ToolResult.fail(f"No visible {args.field} field was found on this page.", "NOT_FOUND")
