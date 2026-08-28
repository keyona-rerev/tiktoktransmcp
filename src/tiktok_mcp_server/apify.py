"""TikTok comments via Apify.

yt-dlp cannot do this. Its TikTok extractor never defines _get_comments, so
the getcomments flag is a silent no-op and comment text never appears. A
second data source is therefore required, and this module is it.

Apify's free plan carries $5 of credit a month and blocks further runs rather
than invoicing when that credit is gone, so the ceiling is a hard one.
"""

from __future__ import annotations

import httpx

from tiktok_mcp_server import config
from tiktok_mcp_server.models import Comment
from tiktok_mcp_server.tiktok import require_tiktok_url

_RUN_SYNC = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"

# Apify hard-stops run-sync at 300 seconds. Ask for less than that so the run,
# not the HTTP client, is the thing that decides when to give up.
_RUN_TIMEOUT = 180
_HTTP_TIMEOUT = 200


class CommentsUnavailable(Exception):
    """Raised when comments cannot be fetched."""


def _first(row: dict, *keys, default=None):
    """Return the first key that carries a value.

    Actor output field names drift between versions. Reading through a list of
    candidates keeps a rename from emptying every row.
    """
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _as_comment(row: dict) -> Comment:
    handle = str(_first(row, "uniqueId", "username", "authorName", default=""))
    reply_to = _first(row, "repliesToId", "replyToId")
    return Comment(
        id=str(_first(row, "cid", "id", default="")),
        text=str(_first(row, "text", "comment", default="")),
        author=handle,
        author_url=f"https://www.tiktok.com/@{handle}" if handle else "",
        likes=_first(row, "diggCount", "likesCount", "likeCount"),
        reply_count=_first(row, "replyCommentTotal", "repliesCount"),
        created_at=str(_first(row, "createTimeISO", "createdAt", default="")),
        is_reply=bool(reply_to),
        reply_to_id=str(reply_to) if reply_to else None,
    )


def get_comments(
    video_url: str,
    limit: int = 50,
    include_replies: bool = False,
) -> list[Comment]:
    """Fetch comments on one TikTok video.

    Apify charges per comment returned, so limit is capped rather than
    unbounded. Replies are off by default because one busy thread multiplies
    the row count, and the cost with it.
    """
    require_tiktok_url(video_url)

    token = config.apify_token()
    if not token:
        raise CommentsUnavailable(
            f"Comments need an Apify API token. Set {config.APIFY_TOKEN_ENV} "
            f"on this service. Every other tool here works without it."
        )

    limit = max(1, min(limit, 500))
    payload = {
        "postURLs": [video_url],
        "commentsPerPost": limit,
        "maxRepliesPerComment": 10 if include_replies else 0,
    }

    url = _RUN_SYNC.format(actor=config.apify_actor())
    try:
        response = httpx.post(
            url,
            params={"token": token, "timeout": _RUN_TIMEOUT, "format": "json"},
            json=payload,
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise CommentsUnavailable(
            "Apify did not finish in time. Try a smaller limit."
        ) from exc

    if response.status_code in (401, 403):
        raise CommentsUnavailable(
            f"Apify rejected the token. Check {config.APIFY_TOKEN_ENV}."
        )
    if response.status_code == 402:
        raise CommentsUnavailable(
            "Apify credit for this month is spent. It resets on the next "
            "billing cycle, and nothing is charged in the meantime."
        )
    if response.status_code >= 400:
        raise CommentsUnavailable(
            f"Apify returned {response.status_code}: {response.text[:200]}"
        )

    rows = response.json()
    if not isinstance(rows, list):
        raise CommentsUnavailable("Apify returned an unexpected response shape.")

    # A run that matched nothing still returns a row, carrying an error note
    # rather than comment text. Dropping empty text filters those out.
    comments = [_as_comment(r) for r in rows if isinstance(r, dict)]
    comments = [c for c in comments if c.text]
    if not comments:
        raise CommentsUnavailable(
            "No comments came back. The video may have comments turned off, "
            "or it may genuinely have none."
        )
    return comments
