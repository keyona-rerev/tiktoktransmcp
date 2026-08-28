"""Data shapes returned by the TikTok tools."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TranscriptSegment:
    """One caption cue."""

    text: str
    start: float
    duration: float


@dataclass
class TranscriptMatch:
    """A caption passage matching a search query."""

    text: str
    start: float
    video_title: str | None = None
    video_url: str | None = None


@dataclass
class Video:
    """A TikTok video."""

    id: str
    title: str
    url: str
    creator: str = ""
    creator_url: str = ""
    description: str = ""
    duration: int | None = None
    upload_date: str = ""
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    repost_count: int | None = None
    thumbnail: str = ""
    caption_languages: list[str] = field(default_factory=list)


@dataclass
class Comment:
    """One comment on a TikTok video."""

    id: str
    text: str
    author: str = ""
    author_url: str = ""
    likes: int | None = None
    reply_count: int | None = None
    created_at: str = ""
    is_reply: bool = False
    reply_to_id: str | None = None


@dataclass
class Creator:
    """A TikTok account."""

    handle: str
    url: str
    display_name: str = ""
    video_count: int | None = None
