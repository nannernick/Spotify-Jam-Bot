"""Who queued what.

Spotify's API returns the queue as one list and doesn't say which songs were added by hand
and which are coming up from the playlist/shuffle. So the bot keeps its own list:
- songs added with /play (with who added them), and
- songs added in the Spotify app, spotted because hand-queued songs are inserted ahead of
  the playlist, while playlist/shuffle songs only ever get appended at the end.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .spotify_service import fmt_track

APP_REQUESTER = "the Spotify app"


@dataclass
class Request:
    uri: str
    label: str
    by: str


def find_app_adds(prev: list[str], items: list[dict]) -> list[dict]:
    """Songs inserted into the queue since the previous check (not appended at the end)."""
    uris = [t["uri"] for t in items]
    new = Counter(uris) - Counter(prev)
    if not new or sum(new.values()) > 5:  # big change = reshuffle or new playlist, not a hand add
        return []
    prev_set = set(prev)
    adds = []
    for i, u in enumerate(uris):
        if new[u] > 0:
            new[u] -= 1
            if any(x in prev_set for x in uris[i + 1:]):
                adds.append(items[i])
    return adds


class QueueTracker:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.pending: list[Request] = []      # requested songs still waiting, in queue order
        self.now: Request | None = None       # requested song playing right now
        self._last_queue: list[str] | None = None
        self._unseen: Counter = Counter()     # /play adds the next check shouldn't count as app adds

    @property
    def queue_empty(self) -> bool:
        return not self.pending

    def note_request(self, track: dict, by: str) -> None:
        self.pending.append(Request(track["uri"], fmt_track(track), by))
        self._unseen[track["uri"]] += 1

    def requester(self, uri: str | None) -> str | None:
        return self.now.by if self.now and self.now.uri == uri else None

    def update(self, current_uri: str | None, queue: list[dict], detect: bool) -> list[dict]:
        """Sync with Spotify's queue. Returns songs newly added in the Spotify app (if detect)."""
        uris = [t["uri"] for t in queue]
        adds: list[dict] = []
        if detect and self._last_queue is not None:
            for t in find_app_adds(self._last_queue, queue):
                if self._unseen[t["uri"]] > 0:
                    self._unseen[t["uri"]] -= 1  # came from /play, already listed
                    continue
                self.pending.append(Request(t["uri"], fmt_track(t), APP_REQUESTER))
                adds.append(t)
        for u in [u for u in self._unseen if u in uris]:
            del self._unseen[u]
        self._last_queue = uris
        self._reconcile(current_uri, uris)
        return adds

    def _reconcile(self, current_uri: str | None, uris: list[str]) -> None:
        """Drop requests that left the queue; the one now playing becomes self.now."""
        remaining = Counter(uris)
        keep = []
        # Duplicates play oldest-first, so keep the newest n copies still in the queue.
        for r in reversed(self.pending):
            if remaining[r.uri] > 0:
                remaining[r.uri] -= 1
                keep.append(r)
            elif r.uri == current_uri:
                self.now = r
        self.pending = list(reversed(keep))
        if self.now and self.now.uri != current_uri:
            self.now = None
