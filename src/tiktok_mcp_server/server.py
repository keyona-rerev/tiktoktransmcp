"""TikTok MCP server."""

from __future__ import annotations

import hmac
import json
import sys
from dataclasses import asdict

from fastmcp import FastMCP

from tiktok_mcp_server import config, tiktok

mcp = FastMCP("TikTok MCP Server")


def _error(exc: Exception) -> str:
    return json.dumps({"error": str(exc)})


def _dump(payload) -> str:
    if isinstance(payload, list):
        return json.dumps([asdict(item) for item in payload], ensure_ascii=False)
    return json.dumps(asdict(payload), ensure_ascii=False)


@mcp.tool()
def get_transcript(video_url: str, language: str = "en") -> str:
    """Get the caption transcript of a TikTok video, with timestamps.

    TikTok carries captions only when the creator enabled them. Silent and
    music-only clips usually have none, and that is reported as an error
    rather than an empty transcript.
    """
    try:
        segments = tiktok.get_transcript(video_url, language)
    except Exception as exc:
        return _error(exc)
    return json.dumps(
        {
            "segment_count": len(segments),
            "text": " ".join(s.text for s in segments),
            "segments": [asdict(s) for s in segments],
        },
        ensure_ascii=False,
    )


@mcp.tool()
def search_transcript(
    video_url: str,
    query: str,
    language: str = "en",
) -> str:
    """Find a phrase in one TikTok video's captions.

    Returns each matching passage with the timestamp it starts at.
    """
    try:
        return _dump(tiktok.search_transcript(video_url, query, language))
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def search_creator_transcripts(
    handle: str,
    query: str,
    language: str = "en",
    max_videos: int = 20,
) -> str:
    """Search a creator's recent videos for a phrase.

    Accepts a handle such as @nasa or a full profile URL. Videos without
    captions are skipped. Returns up to 15 matches with video titles, URLs,
    and timestamps.
    """
    try:
        return _dump(
            tiktok.search_creator_transcripts(handle, query, language, max_videos)
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def get_video_info(video_url: str) -> str:
    """Get details about a TikTok video.

    Includes creator, description, duration, view and like counts, and which
    caption languages exist. Check caption_languages before asking for a
    transcript.
    """
    try:
        return _dump(tiktok.get_video_info(video_url))
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def get_creator_videos(handle: str, limit: int = 20) -> str:
    """List a creator's most recent videos, newest first.

    Accepts a handle such as @nasa or a full profile URL.
    """
    try:
        return _dump(tiktok.get_creator_videos(handle, limit))
    except Exception as exc:
        return _error(exc)


@mcp.tool()
def get_creator_info(handle: str) -> str:
    """Get basic details about a TikTok creator."""
    try:
        return _dump(tiktok.get_creator_info(handle))
    except Exception as exc:
        return _error(exc)


class _TokenGate:
    """ASGI middleware that requires a shared secret on every HTTP request.

    The server is reachable at a public URL with no OAuth, so without this any
    caller who learns the URL can use it. Set MCP_AUTH_TOKEN to turn it on.
    Unset, the server stays open and says so at startup.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        presented = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                presented = value.decode("latin-1").removeprefix("Bearer ").strip()
                break
            if name == b"x-auth-token":
                presented = value.decode("latin-1").strip()
                break

        # compare_digest keeps the check constant-time.
        if not hmac.compare_digest(presented, self.token):
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


def main():
    """Run the MCP server."""
    transport = "stdio"
    if "--transport" in sys.argv:
        idx = sys.argv.index("--transport")
        if idx + 1 < len(sys.argv):
            transport = sys.argv[idx + 1]

    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    import uvicorn
    import yt_dlp

    port = 8000
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    print(f"yt-dlp: {yt_dlp.version.__version__}", file=sys.stderr, flush=True)

    app = mcp.http_app(path="/mcp")
    token = config.auth_token()
    if token:
        print("auth: token required", file=sys.stderr, flush=True)
        app = _TokenGate(app, token)
    else:
        print(
            f"auth: OPEN — anyone with the URL can call this server. "
            f"Set {config.TOKEN_ENV} to require a token.",
            file=sys.stderr,
            flush=True,
        )

    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
