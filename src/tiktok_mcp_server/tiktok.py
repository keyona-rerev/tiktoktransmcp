"""TikTok extraction built on yt-dlp.

Two facts drive the design.

1. TikTok's extractor only populates caption tracks when subtitle extraction
   is explicitly requested. Unlike YouTube's, it returns empty subtitle dicts
   otherwise, which reads as "this video has no captions" and is wrong. Every
   options dict here sets the subtitle flags.

2. Public TikTok videos need no login, so there is no cookie jar.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import yt_dlp

from tiktok_mcp_server.models import (
    Creator,
    TranscriptMatch,
    TranscriptSegment,
    Video,
)

_CAPTION_FORMATS = ("json3", "srv3", "srv1", "vtt", "webvtt")

_TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}

# Subtitle flags are mandatory. See note 1 above.
_BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "socket_timeout": 60,
    "writesubtitles": True,
    "writeautomaticsub": True,
    "subtitleslangs": ["all"],
}


class TranscriptUnavailable(Exception):
    """Raised when a video has no usable caption track."""


def require_tiktok_url(url: str) -> str:
    """Reject any URL that is not on a TikTok host.

    Public because the Apify comment path needs the same guard. A URL reaching
    that code unchecked would let a caller aim a paid API at any host.

    Without this, a URL argument reaches yt-dlp's generic extractor and the
    server becomes an open fetcher for arbitrary hosts, including internal
    addresses reachable from the machine it runs on.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http and https URLs are accepted: {url}")
    if (parsed.hostname or "").lower() not in _TIKTOK_HOSTS:
        raise ValueError(f"Only TikTok URLs are accepted: {url}")
    return url


def _normalize_creator_url(handle_or_url: str) -> str:
    """Turn a handle or URL into a full creator URL."""
    if handle_or_url.startswith("http"):
        return require_tiktok_url(handle_or_url).rstrip("/")
    handle = handle_or_url if handle_or_url.startswith("@") else f"@{handle_or_url}"
    return f"https://www.tiktok.com/{handle}"


def _as_video(info: dict) -> Video:
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    creator = info.get("uploader") or info.get("channel") or ""
    return Video(
        id=str(info.get("id", "")),
        title=info.get("title") or "",
        url=info.get("webpage_url") or info.get("url") or "",
        creator=creator,
        creator_url=info.get("uploader_url") or info.get("channel_url") or "",
        description=info.get("description") or "",
        duration=info.get("duration"),
        upload_date=info.get("upload_date") or "",
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        comment_count=info.get("comment_count"),
        repost_count=info.get("repost_count"),
        thumbnail=info.get("thumbnail") or "",
        caption_languages=sorted({*subs, *auto}),
    )


def get_video_info(video_url: str) -> Video:
    """Get details about a single TikTok video."""
    require_tiktok_url(video_url)
    with yt_dlp.YoutubeDL(_BASE_OPTS) as ydl:
        info = ydl.extract_info(video_url, download=False)
    return _as_video(info)


def get_creator_videos(handle_or_url: str, limit: int = 20) -> list[Video]:
    """List a creator's most recent videos, newest first.

    Runs flat so it stays one request. The entries carry ids and URLs but no
    caption data; call get_video_info or get_transcript on the ones you want.
    """
    url = _normalize_creator_url(handle_or_url)
    opts = {
        **_BASE_OPTS,
        "extract_flat": True,
        "playlistend": max(1, min(limit, 100)),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    videos = []
    for entry in (info.get("entries") or [])[:limit]:
        if not entry:
            continue
        videos.append(
            Video(
                id=str(entry.get("id", "")),
                title=entry.get("title") or "",
                url=entry.get("url") or entry.get("webpage_url") or "",
                creator=entry.get("uploader") or info.get("uploader") or "",
                duration=entry.get("duration"),
                view_count=entry.get("view_count"),
                thumbnail=entry.get("thumbnail") or "",
            )
        )
    return videos


def get_creator_info(handle_or_url: str) -> Creator:
    """Get basic details about a TikTok creator."""
    url = _normalize_creator_url(handle_or_url)
    opts = {**_BASE_OPTS, "extract_flat": True, "playlistend": 1}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    handle = info.get("uploader") or info.get("id") or ""
    return Creator(
        handle=handle if handle.startswith("@") else f"@{handle}",
        url=url,
        display_name=info.get("title") or info.get("channel") or "",
        video_count=info.get("playlist_count"),
    )


def get_transcript(video_url: str, language: str = "en") -> list[TranscriptSegment]:
    """Get the caption transcript of a TikTok video."""
    require_tiktok_url(video_url)

    with yt_dlp.YoutubeDL(_BASE_OPTS) as ydl:
        info = ydl.extract_info(video_url, download=False)
        track = _pick_track(info, language)
        if not track:
            raise TranscriptUnavailable(
                "No captions on this video. TikTok only carries captions when "
                "the creator enabled them, and silent or music-only clips "
                "usually have none."
            )
        fmt = _pick_format(track)
        raw = ydl.urlopen(fmt["url"]).read().decode("utf-8", errors="replace")

    segments = _parse_json3(raw) if fmt.get("ext") == "json3" else _parse_vtt(raw)
    if not segments:
        raise TranscriptUnavailable("The caption track was empty.")
    return segments


def search_transcript(
    video_url: str,
    query: str,
    language: str = "en",
    context_segments: int = 2,
) -> list[TranscriptMatch]:
    """Search one video's captions, returning matches with context."""
    segments = get_transcript(video_url, language)
    needle = query.lower()
    matches = []
    used: set[int] = set()

    for i, segment in enumerate(segments):
        if needle in segment.text.lower() and i not in used:
            start = max(0, i - context_segments)
            end = min(len(segments), i + context_segments + 1)
            parts = []
            for j in range(start, end):
                parts.append(segments[j].text)
                used.add(j)
            matches.append(
                TranscriptMatch(text=" ".join(parts), start=segments[start].start)
            )
    return matches


def search_creator_transcripts(
    handle_or_url: str,
    query: str,
    language: str = "en",
    max_videos: int = 20,
) -> list[TranscriptMatch]:
    """Search a creator's recent videos for a phrase.

    Videos without captions are skipped rather than failing the search, since
    a creator's feed reliably mixes captioned and uncaptioned clips.
    """
    videos = get_creator_videos(handle_or_url, limit=max_videos)
    results = []

    def _one(video: Video) -> list[TranscriptMatch]:
        try:
            found = search_transcript(video.url, query, language, context_segments=1)
        except Exception:
            return []
        for match in found:
            match.video_title = video.title
            match.video_url = video.url
            if len(match.text) > 300:
                match.text = match.text[:300] + "…"
        return found[:3]

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_one, v) for v in videos]
        for future in as_completed(futures):
            results.extend(future.result())
            if len(results) >= 15:
                break
    return results[:15]


def _flat_list(url: str, limit: int) -> list[Video]:
    """Pull a flat list of videos from any TikTok listing page.

    Flat keeps this to one request. Entries carry ids and URLs but no caption
    data, so call get_video_info or get_transcript on the ones worth reading.
    """
    opts = {
        **_BASE_OPTS,
        "extract_flat": True,
        "playlistend": max(1, min(limit, 100)),
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise ValueError(
            "TikTok returned nothing for that listing. The tag, sound, or "
            "collection may not exist, or TikTok may be refusing list "
            "requests right now."
        )

    videos = []
    for entry in (info.get("entries") or [])[:limit]:
        if not entry:
            continue
        videos.append(
            Video(
                id=str(entry.get("id", "")),
                title=entry.get("title") or entry.get("description") or "",
                url=entry.get("webpage_url") or entry.get("url") or "",
                creator=entry.get("uploader") or entry.get("channel") or "",
                duration=entry.get("duration"),
                view_count=entry.get("view_count"),
                like_count=entry.get("like_count"),
                thumbnail=entry.get("thumbnail") or "",
            )
        )
    return videos


def get_hashtag_videos(tag: str, limit: int = 20) -> list[Video]:
    """List videos posted under a hashtag."""
    tag = tag.lstrip("#").strip()
    if not tag:
        raise ValueError("A hashtag is required.")
    return _flat_list(f"https://www.tiktok.com/tag/{tag}", limit)


def get_sound_videos(sound_url: str, limit: int = 20) -> list[Video]:
    """List videos using a sound.

    Takes the full music URL rather than a name, because TikTok identifies a
    sound by the numeric id at the end of that URL and two sounds can share a
    title.
    """
    require_tiktok_url(sound_url)
    return _flat_list(sound_url, limit)


def get_collection_videos(collection_url: str, limit: int = 20) -> list[Video]:
    """List videos in a creator's public collection."""
    require_tiktok_url(collection_url)
    return _flat_list(collection_url, limit)


def _pick_track(info: dict, language: str) -> list[dict] | None:
    """Choose a caption track. Creator-supplied captions win over generated."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    wanted = [language, language.split("-")[0], "en"]

    for source in (manual, auto):
        for code in wanted:
            if code in source:
                return source[code]
        # TikTok labels tracks regionally, e.g. eng-US rather than en.
        for code in wanted:
            for key in source:
                base = key.split("-")[0].lower()
                if base == code.lower() or (code == "en" and base == "eng"):
                    return source[key]
    # Nothing matched the request. Any track beats none.
    for source in (manual, auto):
        for key in source:
            return source[key]
    return None


def _pick_format(track: list[dict]) -> dict:
    for ext in _CAPTION_FORMATS:
        for fmt in track:
            if fmt.get("ext") == ext and fmt.get("url"):
                return fmt
    for fmt in track:
        if fmt.get("url"):
            return fmt
    raise TranscriptUnavailable("No downloadable caption format found.")


def _parse_json3(raw: str) -> list[TranscriptSegment]:
    data = json.loads(raw)
    segments = []
    for event in data.get("events", []):
        text = "".join(s.get("utf8", "") for s in event.get("segs", [])).strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                text=text,
                start=event.get("tStartMs", 0) / 1000.0,
                duration=event.get("dDurationMs", 0) / 1000.0,
            )
        )
    return segments


def _ts_to_seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    if len(parts) == 2:
        parts.insert(0, "0")
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_vtt(raw: str) -> list[TranscriptSegment]:
    segments = []
    cue = re.compile(
        r"((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})\s+-->\s+((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})"
    )

    for block in re.split(r"\n\s*\n", raw):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        match = None
        text_lines = []
        for line in lines:
            found = cue.search(line)
            if found and match is None:
                match = found
                continue
            if match is not None:
                text_lines.append(re.sub(r"<[^>]+>", "", line))
        if match is None:
            continue
        text = " ".join(text_lines).strip()
        if not text:
            continue
        start = _ts_to_seconds(match.group(1))
        end = _ts_to_seconds(match.group(2))
        segments.append(
            TranscriptSegment(text=text, start=start, duration=max(end - start, 0.0))
        )
    return segments
