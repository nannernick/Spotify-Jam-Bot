"""Settings: definitions (with setup guides), loading, validation and saving of .env."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
STATE_FILE = ROOT / "state.json"
SPOTIFY_CACHE = ROOT / ".spotify_cache"


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    section: str
    guide: str
    default: str = ""
    required: bool = False
    secret: bool = False
    advanced: bool = False
    kind: str = "text"  # text | int | float


SETTINGS: list[Setting] = [
    # ---- Discord ----
    Setting(
        "DISCORD_TOKEN", "Bot token", "Discord",
        "1. Go to discord.com/developers/applications and open your app.\n"
        "2. Click Bot in the sidebar, then Reset Token, then Copy.\n"
        "3. Paste it here. Never share it or show it on screen. If it leaks, reset it again.",
        required=True, secret=True,
    ),
    Setting(
        "GUILD_ID", "Server ID", "Discord",
        "1. In Discord: Settings > Advanced > turn on Developer Mode.\n"
        "2. Right-click your server icon > Copy Server ID.\n"
        "Makes slash commands appear instantly in that server.",
        kind="int",
    ),
    Setting(
        "HOST_DISCORD_ID", "Your user ID (host)", "Discord",
        "With Developer Mode on, right-click your own name > Copy User ID.\n"
        "The host can /setjam, /endjam, skip instantly, and /join without a Jam\n"
        "(the bot then streams your Spotify directly).",
        required=True, kind="int",
    ),
    Setting(
        "NOW_PLAYING_CHANNEL_ID", "Now-playing channel ID", "Discord",
        "Right-click a text channel > Copy Channel ID.\n"
        "While the bot is in voice it posts each new song there, plus songs queued in the Spotify app.\n"
        "Leave blank to turn these posts off.",
        kind="int",
    ),
    Setting(
        "SKIP_VOTES", "Skip votes needed", "Discord",
        "How many /skip votes a song needs. The host always skips instantly.",
        default="2", kind="int",
    ),
    # ---- Spotify ----
    Setting(
        "SPOTIFY_CLIENT_ID", "Client ID", "Spotify",
        "1. Go to developer.spotify.com/dashboard (log in with your Premium account).\n"
        "2. Open your app (or Create app: Redirect URI http://127.0.0.1:8888/callback, tick Web API).\n"
        "3. Settings > copy Client ID.",
        required=True,
    ),
    Setting(
        "SPOTIFY_CLIENT_SECRET", "Client secret", "Spotify",
        "Same Settings page > View client secret > copy.\n"
        "If it ever leaks, click Rotate client secret and paste the new one here.",
        required=True, secret=True,
    ),
    Setting(
        "SPOTIFY_REDIRECT_URI", "Redirect URI", "Spotify",
        "Must match the Redirect URI in your Spotify app settings exactly.\n"
        "If you change it, log in to Spotify again from the main menu.",
        default="http://127.0.0.1:8888/callback",
    ),
    # ---- Voice audio ----
    Setting(
        "AUDIO_DEVICE", "Audio device", "Voice audio",
        "The recording device the bot streams into voice. With VB-CABLE this is\n"
        "'CABLE Output (VB-Audio Virtual Cable)'. Set Spotify's output to 'CABLE Input' in\n"
        "Windows Settings > System > Sound > Volume mixer.\n"
        "Use 'Audio check' in the main menu to list exact device names and pick one.",
        default="CABLE Output (VB-Audio Virtual Cable)",
    ),
    Setting(
        "AUDIO_GAIN", "Volume boost", "Voice audio",
        "Volume multiplier for the voice stream. 1 = unchanged.\n"
        "Too quiet: try 1.5 to 2. Distorted or 'bass boosted': lower it (e.g. 0.8).",
        default="1", kind="float",
    ),
    Setting(
        "FFMPEG_PATH", "FFmpeg path", "Voice audio",
        "Path to ffmpeg.exe, or just 'ffmpeg' if it works in a terminal.\n"
        "Install with: winget install Gyan.FFmpeg. 'Audio check' in the main menu can find it for you.",
        default="ffmpeg",
    ),
    # ---- Advanced ----
    Setting(
        "AUDIO_CHANNELS", "Capture channels", "Advanced",
        "Leave blank (recommended). Only set 2 if audio is very quiet and your cable is 16-channel.\n"
        "If /join goes silent after setting it, clear it again.",
        advanced=True, kind="int",
    ),
    Setting(
        "AUDIO_INPUT_FORMAT", "Capture backend", "Advanced",
        "FFmpeg capture backend: dshow on Windows, pulse on Linux, avfoundation on Mac.",
        default="dshow", advanced=True,
    ),
    Setting(
        "POLL_SECONDS", "Spotify check interval", "Advanced",
        "Seconds between checks for song changes and songs queued in the Spotify app.\n"
        "Lower = faster messages but more Spotify API calls. Default 15.",
        default="15", advanced=True, kind="int",
    ),
]
BY_KEY = {s.key: s for s in SETTINGS}


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int | None
    host_id: int | None
    now_playing_channel_id: int | None
    skip_votes: int
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    audio_device: str
    audio_gain: float
    ffmpeg_path: str
    audio_channels: int | None
    audio_input_format: str
    poll_seconds: int


def read_values() -> dict[str, str]:
    """Current settings as strings: .env values over defaults."""
    raw = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
    values = {s.key: s.default for s in SETTINGS}
    for k, v in raw.items():
        if v is not None:
            values[k] = v.strip()
    return values


def check_value(setting: Setting, value: str) -> str | None:
    """Return an error message, or None if the value is OK."""
    if not value:
        return f"{setting.label} is required." if setting.required else None
    if setting.kind == "int" and not value.isdigit():
        return f"{setting.label} must be a whole number."
    if setting.kind == "float":
        try:
            if float(value) <= 0:
                return f"{setting.label} must be above 0."
        except ValueError:
            return f"{setting.label} must be a number like 1 or 1.5."
    return None


def validate(values: dict[str, str]) -> list[str]:
    return [err for s in SETTINGS if (err := check_value(s, values.get(s.key, "")))]


def save_values(values: dict[str, str]) -> None:
    """Rewrite .env grouped by section; keeps any extra keys the user added."""
    lines = ["# Spotify Jam Bot settings. Edit with: python run.py  (menu option 2)", ""]
    section = None
    for s in SETTINGS:
        if s.section != section:
            section = s.section
            lines += ["", f"# ---- {section} ----"] if len(lines) > 2 else [f"# ---- {section} ----"]
        lines.append(f"{s.key}={values.get(s.key, '')}")
    extras = {k: v for k, v in values.items() if k not in BY_KEY}
    if extras:
        lines += ["", "# ---- Other ----"] + [f"{k}={v}" for k, v in extras.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _int(v: str) -> int | None:
    return int(v) if v and v.isdigit() else None


def load_config() -> Config:
    v = read_values()
    return Config(
        discord_token=v["DISCORD_TOKEN"],
        guild_id=_int(v["GUILD_ID"]),
        host_id=_int(v["HOST_DISCORD_ID"]),
        now_playing_channel_id=_int(v["NOW_PLAYING_CHANNEL_ID"]),
        skip_votes=_int(v["SKIP_VOTES"]) or 2,
        spotify_client_id=v["SPOTIFY_CLIENT_ID"],
        spotify_client_secret=v["SPOTIFY_CLIENT_SECRET"],
        spotify_redirect_uri=v["SPOTIFY_REDIRECT_URI"] or BY_KEY["SPOTIFY_REDIRECT_URI"].default,
        audio_device=v["AUDIO_DEVICE"],
        audio_gain=float(v["AUDIO_GAIN"] or 1),
        ffmpeg_path=v["FFMPEG_PATH"] or "ffmpeg",
        audio_channels=_int(v["AUDIO_CHANNELS"]),
        audio_input_format=v["AUDIO_INPUT_FORMAT"] or "dshow",
        poll_seconds=max(5, _int(v["POLL_SECONDS"]) or 15),
    )
