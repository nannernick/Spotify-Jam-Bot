"""The Discord side: slash commands, voice streaming and now-playing posts."""

from __future__ import annotations

import asyncio
import json
import logging

import discord
import spotipy
from discord import app_commands
from discord.ext import commands, tasks

from . import audio
from .config import STATE_FILE, Config
from .spotify_service import CONTEXT_RE, TRACK_RE, SpotifyService, error_message, fmt_duration, fmt_track
from .tracking import APP_REQUESTER, QueueTracker

log = logging.getLogger("jambot")

NO_JAM = "No Jam going. Host: start a Jam in Spotify, then run /setjam with the invite link."


class State:
    """Small JSON file so the Jam link/status survive restarts."""

    def __init__(self) -> None:
        data = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
        self.jam_link: str = data.get("jam_link", "")
        self.jam_active: bool = data.get("jam_active", False)

    def save(self) -> None:
        STATE_FILE.write_text(json.dumps({"jam_link": self.jam_link, "jam_active": self.jam_active}, indent=2))


class PickView(discord.ui.View):
    """Dropdown of search results; only the person who searched can pick."""

    def __init__(self, options: list[tuple[str, str, str]], owner_id: int, on_pick):
        super().__init__(timeout=60)
        self.owner_id, self.on_pick = owner_id, on_pick
        self.select = discord.ui.Select(placeholder="Pick one", options=[
            discord.SelectOption(label=label[:100], description=desc[:100] or None, value=value)
            for label, desc, value in options
        ])
        self.select.callback = self._picked
        self.add_item(self.select)

    async def _picked(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's someone else's search.", ephemeral=True)
            return
        try:
            msg = await self.on_pick(self.select.values[0], interaction.user)
        except spotipy.SpotifyException as e:
            msg = error_message(e)
        await interaction.response.edit_message(content=msg, view=None)
        self.stop()


class JamCog(commands.Cog):
    def __init__(self, bot: commands.Bot, cfg: Config, spotify: SpotifyService):
        self.bot, self.cfg, self.sp = bot, cfg, spotify
        self.state = State()
        self.tracker = QueueTracker()
        self.skip_votes: tuple[str | None, set[int]] = (None, set())
        self.last_announced: str | None = None
        self.now_playing_msg: discord.Message | None = None  # deleted when the next song starts
        self.next_station: tuple[str, str] | None = None     # (uri, name) to switch to when the queue empties

    async def cog_load(self):
        self.poll.change_interval(seconds=self.cfg.poll_seconds)
        self.poll.start()

    async def cog_unload(self):
        self.poll.cancel()

    # ---------- helpers ----------
    def is_host(self, user: discord.abc.User) -> bool:
        return bool(self.cfg.host_id) and user.id == self.cfg.host_id

    @property
    def streaming(self) -> bool:
        return any(vc.is_connected() for vc in self.bot.voice_clients)

    async def post(self, text: str) -> discord.Message | None:
        channel = self.bot.get_channel(self.cfg.now_playing_channel_id or 0)
        return await channel.send(text) if channel else None

    async def post_now_playing(self, text: str) -> None:
        """Replace the previous now-playing post so only the current song shows. Queued posts stay."""
        await self.clear_now_playing()
        self.now_playing_msg = await self.post(text)

    async def clear_now_playing(self) -> None:
        if self.now_playing_msg:
            try:
                await self.now_playing_msg.delete()
            except discord.HTTPException:
                pass  # already deleted by someone
            self.now_playing_msg = None

    async def refresh(self) -> dict | None:
        """Fetch playback + queue, update who-queued-what, post songs added in the Spotify app."""
        playback = await self.sp.playback()
        queue = await self.sp.queue()
        current = playback["item"]["uri"] if playback and playback.get("item") else None
        for t in self.tracker.update(current, queue, detect=self.streaming):
            await self.post(f"➕ Queued in Spotify: {fmt_track(t)}")
        return playback

    async def source_label(self, playback: dict) -> str:
        if by := self.tracker.requester(playback["item"]["uri"]):
            return "queued in the Spotify app" if by == APP_REQUESTER else f"requested by **{by}**"
        return await self.sp.context_label(playback)

    async def now_line(self, playback: dict) -> str:
        src = await self.source_label(playback)
        return f"🎵 Now playing: {fmt_track(playback['item'])}" + (f" ({src})" if src else "")

    async def queue_track(self, uri_or_track, user: discord.abc.User) -> str:
        if isinstance(uri_or_track, dict):
            track = uri_or_track
        else:
            track = await self.sp.track(TRACK_RE.search(uri_or_track).group(1))
        await self.sp.add_to_queue(track["uri"])
        self.tracker.note_request(track, user.display_name)
        return f"➕ Queued {fmt_track(track)} (by {user.mention})"

    async def switch_station(self, uri: str) -> str:
        name = await self.sp.context_name(uri)
        await self.sp.play_context(uri)
        self.next_station = None
        return name

    # ---------- voice ----------
    def start_stream(self, vc: discord.VoiceClient) -> None:
        log.info("Streaming %r into %s", self.cfg.audio_device, vc.channel)

        def after(err):
            if err:
                log.warning("Audio stream stopped: %s", err)
            asyncio.run_coroutine_threadsafe(self._restart_stream(vc), self.bot.loop)

        vc.play(audio.capture_source(self.cfg), after=after)

    async def _restart_stream(self, vc: discord.VoiceClient) -> None:
        """Keep streaming if FFmpeg dies or a test tone ends."""
        await asyncio.sleep(2)
        if vc.is_connected() and not vc.is_playing():
            self.start_stream(vc)

    async def leave_voice(self, guild: discord.Guild | None) -> None:
        if guild and guild.voice_client:
            await guild.voice_client.disconnect()
        await self.clear_now_playing()
        self.last_announced = None

    async def end_jam(self, guild: discord.Guild | None) -> None:
        self.state.jam_active = False
        self.state.save()
        self.tracker.reset()
        self.next_station = None
        await self.leave_voice(guild)

    # ---------- errors ----------
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        if isinstance(original, spotipy.SpotifyException):
            msg = error_message(original)
        else:
            log.error("Command failed", exc_info=original)
            msg = f"Something went wrong: {original}"
        if interaction.response.is_done():
            await interaction.followup.send(msg)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ---------- Jam ----------
    @app_commands.command(description="Get the Jam link and what's playing")
    async def jam(self, interaction: discord.Interaction):
        if not self.state.jam_active:
            await interaction.response.send_message(NO_JAM)
            return
        await interaction.response.defer()
        playback = await self.refresh()
        now = "\n" + await self.now_line(playback) if playback and playback.get("item") else ""
        await interaction.followup.send(f"🎧 Join the Jam: {self.state.jam_link}{now}")

    @app_commands.command(description="(Host) Start the Jam with your Spotify Jam invite link")
    @app_commands.describe(link="Spotify: Start a Jam > Invite > Copy link")
    async def setjam(self, interaction: discord.Interaction, link: str):
        if not self.is_host(interaction.user):
            await interaction.response.send_message("Only the host can set the Jam.", ephemeral=True)
            return
        self.state.jam_link, self.state.jam_active = link.strip(), True
        self.state.save()
        await interaction.response.send_message(
            f"✅ Jam is on. Join here: {self.state.jam_link}\nUse /join to stream it in voice, /endjam when you're done.")

    @app_commands.command(description="(Host) End the Jam: bot leaves voice and stops posting")
    async def endjam(self, interaction: discord.Interaction):
        if not self.is_host(interaction.user):
            await interaction.response.send_message("Only the host can end the Jam.", ephemeral=True)
            return
        await self.end_jam(interaction.guild)
        await interaction.response.send_message("🛑 Jam ended.")

    # ---------- queue ----------
    @app_commands.command(description="Add a song to the queue")
    @app_commands.describe(query="Start typing a song, then pick it from the list (or paste a Spotify link)")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        if TRACK_RE.search(query):
            await interaction.followup.send(await self.queue_track(query, interaction.user))
            return
        tracks = await self.sp.search_tracks(query, limit=5)
        if not tracks:
            await interaction.followup.send(f"No results for `{query}`.")
            return
        options = [(t["name"], f"{', '.join(a['name'] for a in t['artists'])} ({fmt_duration(t['duration_ms'])})", t["uri"])
                   for t in {t["uri"]: t for t in tracks}.values()]
        await interaction.followup.send("Which one?", view=PickView(options, interaction.user.id, self.queue_track))

    @play.autocomplete("query")
    async def play_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.strip()
        if len(current) < 2 or TRACK_RE.search(current):
            return []
        try:
            tracks = await self.sp.search_tracks(current)
        except Exception:
            return []
        choices: dict[str, str] = {}
        for t in tracks:
            name = f"{t['name']} - {', '.join(a['name'] for a in t['artists'])} ({fmt_duration(t['duration_ms'])})"
            choices.setdefault(t["uri"], name if len(name) <= 100 else name[:97] + "...")
        return [app_commands.Choice(name=n, value=u) for u, n in choices.items()]

    @app_commands.command(name="queue", description="Songs people have queued")
    async def show_queue(self, interaction: discord.Interaction):
        await interaction.response.defer()
        playback = await self.refresh()
        playing = playback and playback.get("item")
        text = (await self.now_line(playback) + "\n\n") if playing else ""
        if self.tracker.pending:
            lines = [f"`{i}.` {r.label} — {r.by}" for i, r in enumerate(self.tracker.pending[:15], 1)]
            extra = len(self.tracker.pending) - 15
            text += "**Up next:**\n" + "\n".join(lines) + (f"\n…and {extra} more" if extra > 0 else "")
        else:
            then = await self.sp.context_label(playback) if playing else ""
            text += "Nobody's queued anything. Add a song with /play." + (f" Then {then}." if then else "")
        if self.next_station:
            text += f"\n📻 Switching to **{self.next_station[1]}** when the queue is empty."
        await interaction.followup.send(text)

    @app_commands.command(description="Show the current song")
    async def np(self, interaction: discord.Interaction):
        await interaction.response.defer()
        playback = await self.refresh()
        if not playback or not playback.get("item"):
            await interaction.followup.send("Nothing playing.")
            return
        t = playback["item"]
        status = "▶️" if playback["is_playing"] else "⏸️"
        embed = discord.Embed(
            title=t["name"], url=t["external_urls"]["spotify"], color=0x1DB954,
            description=f"{', '.join(a['name'] for a in t['artists'])}\n"
                        f"{status} {fmt_duration(playback['progress_ms'])} / {fmt_duration(t['duration_ms'])}",
        )
        if t["album"]["images"]:
            embed.set_thumbnail(url=t["album"]["images"][0]["url"])
        if src := await self.source_label(playback):
            embed.set_footer(text=src.replace("**", ""))
        await interaction.followup.send(embed=embed)

    @app_commands.command(description="Vote to skip (host skips instantly)")
    async def skip(self, interaction: discord.Interaction):
        await interaction.response.defer()
        playback = await self.sp.playback()
        if not playback or not playback.get("item"):
            await interaction.followup.send("Nothing playing.")
            return
        track_id, voters = self.skip_votes
        if track_id != playback["item"]["id"]:
            track_id, voters = playback["item"]["id"], set()
        voters.add(interaction.user.id)
        self.skip_votes = (track_id, voters)
        song = fmt_track(playback["item"])
        if self.is_host(interaction.user) or len(voters) >= self.cfg.skip_votes:
            await self.sp.next()
            self.skip_votes = (None, set())
            await interaction.followup.send(f"⏭️ Skipped {song}")
        else:
            await interaction.followup.send(f"🗳️ Skip vote {len(voters)}/{self.cfg.skip_votes} for {song}")

    @app_commands.command(description="Pause playback")
    async def pause(self, interaction: discord.Interaction):
        await self.sp.pause()
        await interaction.response.send_message(f"⏸️ Paused by {interaction.user.mention}")

    @app_commands.command(description="Resume playback")
    async def resume(self, interaction: discord.Interaction):
        await self.sp.resume()
        await interaction.response.send_message(f"▶️ Resumed by {interaction.user.mention}")

    # ---------- station ----------
    @app_commands.command(description="Change the playlist/album/artist it shuffles when nobody's queued anything")
    @app_commands.describe(source="Start typing a playlist, album or artist (or paste a Spotify link)",
                           now="Switch right away instead of waiting for the queued songs to finish")
    async def station(self, interaction: discord.Interaction, source: str, now: bool = False):
        if not (self.state.jam_active or self.streaming or self.is_host(interaction.user)):
            await interaction.response.send_message(NO_JAM, ephemeral=True)
            return
        await interaction.response.defer()
        if m := CONTEXT_RE.search(source):
            uri = f"spotify:{m.group(1)}:{m.group(2)}"
        else:
            found = await self.sp.search_contexts(source)
            if not found:
                await interaction.followup.send(f"No playlist, album or artist found for `{source}`.")
                return
            uri = found[0][1]
        await self.refresh()
        by = interaction.user.mention
        if now or (self.tracker.queue_empty and self.tracker.now is None):
            name = await self.switch_station(uri)
            await interaction.followup.send(f"📻 Now shuffling **{name}** (set by {by})")
        else:
            name = await self.sp.context_name(uri)
            self.next_station = (uri, name)
            await interaction.followup.send(f"📻 Switching to **{name}** once the queued songs finish (set by {by}).")

    @station.autocomplete("source")
    async def station_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.strip()
        if len(current) < 2 or CONTEXT_RE.search(current):
            return []
        try:
            found = await self.sp.search_contexts(current)
        except Exception:
            return []
        choices: dict[str, str] = {}
        for label, uri in found:
            choices.setdefault(uri, label if len(label) <= 100 else label[:97] + "...")
        return [app_commands.Choice(name=n, value=u) for u, n in list(choices.items())[:25]]

    # ---------- voice commands ----------
    @app_commands.command(description="Bot joins your voice channel and streams the music")
    async def join(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
            return
        if not self.state.jam_active and not self.is_host(member):
            await interaction.response.send_message(NO_JAM, ephemeral=True)
            return
        await interaction.response.defer()
        channel = member.voice.channel
        vc = interaction.guild.voice_client
        if vc:
            await vc.move_to(channel)
        else:
            vc = await channel.connect(self_deaf=True)
            self.tracker.reset()
        if not vc.is_playing():
            self.start_stream(vc)
        await asyncio.sleep(2)
        source = "the Jam" if self.state.jam_active else "your Spotify (no Jam running)"
        warn = "" if vc.is_playing() else "\n⚠️ Audio isn't running. Check the bot window for FFmpeg errors."
        await interaction.followup.send(f"🔊 Streaming {source} in **{channel.name}**. /leave to stop.{warn}")

    @app_commands.command(description="Bot leaves the voice channel")
    async def leave(self, interaction: discord.Interaction):
        if not (interaction.guild and interaction.guild.voice_client):
            await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)
            return
        await self.leave_voice(interaction.guild)
        await interaction.response.send_message("👋 Left the voice channel.")

    @app_commands.command(description="Play a 5-second beep in voice to test audio")
    async def testtone(self, interaction: discord.Interaction):
        vc = interaction.guild and interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("Use /join first.", ephemeral=True)
            return
        vc.stop()  # the music stream restarts by itself after the beep
        vc.play(audio.tone_source(self.cfg),
                after=lambda e: asyncio.run_coroutine_threadsafe(self._restart_stream(vc), self.bot.loop))
        await interaction.response.send_message("🔔 Playing a 5-second beep. Music resumes after it.")

    @app_commands.command(name="help", description="List the bot's commands")
    async def help_command(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "**Jam**: /jam · /setjam (host) · /endjam (host)\n"
            "**Queue**: /play · /queue · /np · /skip · /pause · /resume\n"
            "**Station**: /station: change what it shuffles when nobody's queued anything\n"
            "**Voice**: /join · /leave · /testtone", ephemeral=True)

    # ---------- background ----------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Everyone else left the call: leave (and end the Jam if one is on)."""
        vc = member.guild.voice_client
        if vc and vc.channel and all(m.bot for m in vc.channel.members):
            if self.state.jam_active:
                await self.end_jam(member.guild)
            else:
                await self.leave_voice(member.guild)

    @tasks.loop(seconds=15)
    async def poll(self):
        if not self.streaming and not self.next_station:
            return
        try:
            playback = await self.refresh()
            if self.next_station and self.tracker.queue_empty and self.tracker.now is None:
                name = await self.switch_station(self.next_station[0])
                await self.post(f"📻 Queue's empty, now shuffling **{name}**")
                return
        except Exception as e:  # keep polling through hiccups
            log.warning("Spotify check failed: %s", e)
            return
        if not self.streaming or not playback or not playback.get("item") or not playback["is_playing"]:
            return
        if playback["item"]["id"] != self.last_announced:
            self.last_announced = playback["item"]["id"]
            await self.post_now_playing(await self.now_line(playback))

    @poll.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()


class JamBot(commands.Bot):
    def __init__(self, cfg: Config):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default(), help_command=None)
        self.cfg = cfg

    async def setup_hook(self):
        await self.add_cog(JamCog(self, self.cfg, SpotifyService(self.cfg)))
        if self.cfg.guild_id:
            guild = discord.Object(id=self.cfg.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        log.info("Logged in as %s. Press Ctrl+C to stop and return to the menu.", self.user)


_logging_ready = False


def run(cfg: Config) -> None:
    """Run until Ctrl+C. Safe to call again from the menu."""
    global _logging_ready
    if not _logging_ready:
        discord.utils.setup_logging(level=logging.INFO)
        _logging_ready = True
    JamBot(cfg).run(cfg.discord_token, log_handler=None)
