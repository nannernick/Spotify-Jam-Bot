"""Voice audio: FFmpeg capture of a system audio device, plus helpers for the audio check menu."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import discord

from .config import Config

DEVICE_RE = re.compile(r'"([^"]+)"\s*\(audio\)')


def capture_source(cfg: Config) -> discord.AudioSource:
    """Live audio from cfg.audio_device (e.g. VB-CABLE output) for the voice channel."""
    fmt = cfg.audio_input_format
    source = f"audio={cfg.audio_device}" if fmt == "dshow" else cfg.audio_device
    before = f"-f {fmt} -audio_buffer_size 50"
    if fmt == "dshow" and cfg.audio_channels:
        before += f" -channels {cfg.audio_channels}"
    filters = [] if cfg.audio_channels else ["pan=stereo|FL=FL|FR=FR"]  # avoid quiet multichannel downmix
    if cfg.audio_gain != 1:
        filters.append(f"volume={cfg.audio_gain}")
    options = f"-af {','.join(filters)}" if filters else None
    return discord.FFmpegPCMAudio(source, executable=cfg.ffmpeg_path, before_options=before, options=options)


def tone_source(cfg: Config) -> discord.AudioSource:
    """5-second 440 Hz beep for /testtone."""
    return discord.FFmpegPCMAudio("sine=frequency=440:duration=5", executable=cfg.ffmpeg_path,
                                  before_options="-f lavfi")


def resolve_ffmpeg(path: str) -> str | None:
    """Working ffmpeg executable, checking the configured path, PATH, then winget's install folder."""
    if path and Path(path).is_file():
        return str(Path(path))
    if found := shutil.which(path or "ffmpeg"):
        return found
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    if winget.is_dir():
        for exe in winget.rglob("ffmpeg.exe"):
            return str(exe)
    return None


def _run(ffmpeg: str, *args: str, timeout: int = 20) -> str:
    proc = subprocess.run([ffmpeg, "-hide_banner", *args], capture_output=True, text=True,
                          timeout=timeout, errors="replace")
    return proc.stdout + proc.stderr


def list_audio_devices(ffmpeg: str) -> list[str]:
    """Audio capture devices FFmpeg can see (Windows/dshow)."""
    out = _run(ffmpeg, "-list_devices", "true", "-f", "dshow", "-i", "dummy")
    return list(dict.fromkeys(DEVICE_RE.findall(out)))


def measure_level(ffmpeg: str, device: str, seconds: int = 5) -> tuple[float | None, float | None, str]:
    """Record a few seconds from the device. Returns (mean dB, max dB, raw output)."""
    out = _run(ffmpeg, "-f", "dshow", "-i", f"audio={device}", "-t", str(seconds),
               "-af", "volumedetect", "-f", "null", "-", timeout=seconds + 15)
    mean = re.search(r"mean_volume:\s*(-?[\d.]+|-inf)", out)
    peak = re.search(r"max_volume:\s*(-?[\d.]+|-inf)", out)
    to_f = lambda m: (float("-inf") if m.group(1) == "-inf" else float(m.group(1))) if m else None
    return to_f(mean), to_f(peak), out
