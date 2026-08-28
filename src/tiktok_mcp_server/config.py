"""Runtime configuration read from the environment."""

from __future__ import annotations

import os

TOKEN_ENV = "MCP_AUTH_TOKEN"


def auth_token() -> str | None:
    """The shared secret required on HTTP requests, if one is configured."""
    return os.environ.get(TOKEN_ENV, "").strip() or None
