"""Spotify login and an async wrapper around the Web API calls the bot uses."""

from __future__ import annotations

import asyncio
import re

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .config import SPOTIFY_CACHE, Config

SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
TRACK_RE = re.compile(r"(?:open\.spotify\.com/(?:intl-[\w-]+/)?track/|spotify:track:)([A-Za-z0-9]{22})")
CONTEXT_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[\w-]+/)?|spotify:)(playlist|album|artist)[/:]([A-Za-z0-9]{22})"
)


def make_auth(cfg: Config) -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=cfg.spotify_client_id,
        client_secret=cfg.spotify_client_secret,
        redirect_uri=cfg.spotify_redirect_uri,
        scope=SCOPES,
        open_browser=False,
        cache_path=str(SPOTIFY_CACHE),
    )


def is_logged_in() -> bool:
    return SPOTIFY_CACHE.exists()


def interactive_login(cfg: Config) -> None:
    auth = make_auth(cfg)
    print("\n1. Open this link and log in with the Spotify account that hosts the Jam:\n")
    print("   " + auth.get_authorize_url())
    print("\n2. After you approve, the browser shows a page that won't load. That's expected.")
    redirect = input("3. Copy the whole address-bar URL and paste it here: ").strip()
    auth.get_access_token(auth.parse_response_code(redirect), as_dict=False)
    print("\nLogged in. Saved to .spotify_cache (keep this file private).")


def fmt_track(t: dict) -> str:
    return f"**{t['name']}** — {', '.join(a['name'] for a in t['artists'])}"


def fmt_duration(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def error_message(e: spotipy.SpotifyException) -> str:
    return {
        401: "Spotify login expired. The host needs to log in again (run.py > Log in to Spotify).",
        403: "Spotify refused that (host needs Premium, or that action isn't allowed right now).",
        404: "No active Spotify device. The host needs Spotify open and playing.",
        429: "Spotify rate limit hit. Try again in a minute.",
    }.get(e.http_status, f"Spotify error: {e.msg}")


class SpotifyService:
    """Every call runs in a thread so the bot's event loop never blocks."""

    def __init__(self, cfg: Config):
        self.sp = spotipy.Spotify(auth_manager=make_auth(cfg))
        self._context_names: dict[str, str] = {}
        self._search_cache: dict[tuple, list] = {}

    async def _call(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    # ---- playback ----
    async def playback(self) -> dict | None:
        return await self._call(self.sp.current_playback)

    async def queue(self) -> list[dict]:
        q = await self._call(self.sp.queue)
        return [t for t in (q.get("queue") or []) if t]

    async def add_to_queue(self, uri: str) -> None:
        await self._call(self.sp.add_to_queue, uri)

    async def next(self) -> None:
        await self._call(self.sp.next_track)

    async def pause(self) -> None:
        await self._call(self.sp.pause_playback)

    async def resume(self) -> None:
        await self._call(self.sp.start_playback)

    async def play_context(self, uri: str) -> None:
        """Switch what's playing to a playlist/album/artist, shuffled."""
        await self._call(self.sp.shuffle, True)
        await self._call(self.sp.start_playback, context_uri=uri)
        await self._call(self.sp.shuffle, True)

    # ---- lookups ----
    async def track(self, track_id: str) -> dict:
        return await self._call(self.sp.track, track_id)

    async def search_tracks(self, query: str, limit: int = 10) -> list[dict]:
        key = ("track", query.lower(), limit)
        if key not in self._search_cache:
            res = await self._call(self.sp.search, q=query, type="track", limit=limit)
            self._remember(key, [t for t in res["tracks"]["items"] if t])
        return self._search_cache[key]

    async def search_contexts(self, query: str) -> list[tuple[str, str]]:
        """(label, uri) for playlists, albums and artists matching the query."""
        key = ("context", query.lower())
        if key not in self._search_cache:
            res = await self._call(self.sp.search, q=query, type="playlist,album,artist", limit=5)
            out: list[tuple[str, str]] = []
            for p in (res.get("playlists") or {}).get("items") or []:
                if p:
                    owner = (p.get("owner") or {}).get("display_name") or "Spotify"
                    out.append((f"Playlist: {p['name']} (by {owner})", p["uri"]))
            for a in (res.get("albums") or {}).get("items") or []:
                if a:
                    out.append((f"Album: {a['name']} — {', '.join(x['name'] for x in a['artists'])}", a["uri"]))
            for a in (res.get("artists") or {}).get("items") or []:
                if a:
                    out.append((f"Artist: {a['name']}", a["uri"]))
            self._remember(key, out)
        return self._search_cache[key]

    async def context_name(self, uri: str, kind: str | None = None) -> str:
        """Name of a playlist/album/artist. Spotify-made mixes are often unreadable in dev mode."""
        if uri not in self._context_names:
            kind = kind or uri.split(":")[1]
            name = None
            try:
                if kind == "playlist":
                    name = (await self._call(self.sp.playlist, uri, fields="name"))["name"]
                elif kind == "album":
                    name = (await self._call(self.sp.album, uri))["name"]
                elif kind == "artist":
                    name = (await self._call(self.sp.artist, uri))["name"]
            except Exception:
                pass
            if kind == "collection" or uri.endswith(":collection"):
                name = "Liked Songs"
            self._context_names[uri] = name or {
                "playlist": "a playlist", "album": "an album", "artist": "an artist"
            }.get(kind, "Spotify")
        return self._context_names[uri]

    async def context_label(self, playback: dict) -> str:
        """e.g. 'shuffling from House Mix' for whatever is playing outside the queue."""
        ctx = playback.get("context") or {}
        if not ctx.get("uri"):
            return ""
        name = await self.context_name(ctx["uri"], ctx.get("type"))
        verb = "shuffling from" if playback.get("shuffle_state") else "playing from"
        return f"{verb} {name}"

    def _remember(self, key, value) -> None:
        self._search_cache[key] = value
        if len(self._search_cache) > 300:
            self._search_cache.pop(next(iter(self._search_cache)))
