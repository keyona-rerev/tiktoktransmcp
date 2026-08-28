"""Runtime configuration read from the environment."""

from __future__ import annotations

import os

TOKEN_ENV = "MCP_AUTH_TOKEN"
APIFY_TOKEN_ENV = "APIFY_API_TOKEN"
APIFY_ACTOR_ENV = "APIFY_COMMENTS_ACTOR"

# Comments are the one capability yt-dlp cannot supply, so they run through an
# Apify actor instead. The default is overridable by environment variable: if
# this actor is retired or repriced, swapping it needs a variable change on the
# service rather than a code change and a redeploy.
DEFAULT_APIFY_ACTOR = "clockworks~tiktok-comments-scraper"


def auth_token() -> str | None:
    """The shared secret required on HTTP requests, if one is configured."""
    return os.environ.get(TOKEN_ENV, "").strip() or None


def apify_token() -> str | None:
    """The Apify API token used to fetch comments, if one is configured.

    Absent, every other tool still works. Only get_comments fails, and it says
    which variable is missing.
    """
    return os.environ.get(APIFY_TOKEN_ENV, "").strip() or None


def apify_actor() -> str:
    """The Apify actor that fetches comments."""
    return os.environ.get(APIFY_ACTOR_ENV, "").strip() or DEFAULT_APIFY_ACTOR
