"""Spotify Jam Bot launcher: start the bot, change settings, log in to Spotify, check audio.

    python run.py          open the menu
    python run.py --start  start the bot straight away
"""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version

from jambot import __version__
from jambot import audio
from jambot.config import BY_KEY, ROOT, SETTINGS, check_value, load_config, read_values, save_values, validate
from jambot.spotify_service import interactive_login, is_logged_in

LINE = "-" * 60

SETUP_GUIDE = """
SETUP CHECKLIST (do these once)

 1. Discord bot: discord.com/developers/applications > New Application.
    Bot tab > Reset Token > copy it into Settings > Bot token.
    Invite it with this link (put your Application ID in place of APP_ID):
    https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot+applications.commands&permissions=3164160

 2. Discord IDs: Discord Settings > Advanced > Developer Mode on.
    Right-click your server > Copy Server ID. Right-click yourself > Copy User ID.

 3. Spotify app: developer.spotify.com/dashboard > Create app.
    Redirect URI: http://127.0.0.1:8888/callback, tick Web API, Save.
    Copy Client ID and Client secret into Settings.

 4. Spotify login: main menu option 3.

 5. Voice audio (Windows):
    - winget install Gyan.FFmpeg
    - Install VB-CABLE from vb-audio.com/Cable (Run as administrator > Install Driver) and restart.
    - Sound settings: keep your headset as the default AND default communication device
      (VB-CABLE sometimes takes these over).
    - Volume mixer > Spotify > Output device: CABLE Input.
    - Main menu option 4 to check it all.

EACH SESSION
 - Play something in Spotify. Start a Jam > Invite > Copy link.
 - Start the bot (option 1). In Discord: /setjam <link>, then /join.
 - Host only: /join without a Jam streams your Spotify directly.
 - Done: /endjam. Set Spotify's output back to Default if you want to hear it normally.
"""


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "0"


def pause() -> None:
    ask("\nPress Enter to go back...")


def show_value(key: str, value: str) -> str:
    s = BY_KEY.get(key)
    if not value:
        return "(not set)"
    if s and s.secret:
        return f"set (…{value[-4:]})"
    return value


def voice_status() -> str:
    try:
        v = version("davey")
    except PackageNotFoundError:
        return "MISSING: run  pip install -r requirements.txt"
    if v.startswith("0.2"):
        return f"davey {v} makes the bot silent. Run:  pip install \"davey<0.2\""
    return "OK"


def status_lines(values: dict[str, str]) -> list[str]:
    problems = validate(values)
    ffmpeg = audio.resolve_ffmpeg(values.get("FFMPEG_PATH", "ffmpeg"))
    return [
        f"Settings:       {'OK' if not problems else f'{len(problems)} to fix (option 2)'}",
        f"Spotify login:  {'OK' if is_logged_in() else 'not logged in (option 3)'}",
        f"FFmpeg:         {'OK' if ffmpeg else 'not found (option 4)'}",
        f"Voice library:  {voice_status()}",
    ]


# ---------------- start ----------------
def start_bot() -> None:
    values = read_values()
    problems = validate(values)
    if problems:
        print("\nFix these first (option 2):")
        for p in problems:
            print("  - " + p)
        pause()
        return
    cfg = load_config()
    if not is_logged_in():
        print("\nYou're not logged in to Spotify yet.")
        if ask("Log in now? (y/n): ").lower() == "y":
            interactive_login(cfg)
        else:
            return
    from jambot.bot import run  # imported here so the menu works even if discord.py is broken

    print(f"\n{LINE}\nStarting the bot. Press Ctrl+C to stop and come back to the menu.\n{LINE}")
    try:
        run(cfg)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\nThe bot stopped with an error: {e}")
        if "Improper token" in str(e) or "401" in str(e):
            print("Your Discord bot token looks wrong. Reset it and update it in option 2.")
        pause()


# ---------------- settings ----------------
def settings_menu() -> None:
    show_advanced = False
    while True:
        values = read_values()
        visible = [s for s in SETTINGS if show_advanced or not s.advanced]
        print(f"\n{LINE}\nSETTINGS   (* = required)\n{LINE}")
        section = None
        for i, s in enumerate(visible, 1):
            if s.section != section:
                section = s.section
                print(f"\n  {section}")
            flag = "*" if s.required else " "
            err = " <- needs fixing" if check_value(s, values.get(s.key, "")) else ""
            print(f"  {i:>2}) {s.label + flag:<26} {show_value(s.key, values.get(s.key, ''))}{err}")
        print(f"\n   a) {'Hide' if show_advanced else 'Show'} advanced settings")
        print("   0) Back")
        choice = ask("\nPick a setting to change: ").lower()
        if choice == "0" or choice == "":
            return
        if choice == "a":
            show_advanced = not show_advanced
            continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(visible):
            print("Not an option.")
            continue
        edit_setting(visible[int(choice) - 1], values)


def edit_setting(s, values: dict[str, str]) -> None:
    current = values.get(s.key, "")
    print(f"\n{LINE}\n{s.label}  ({s.key})\n{LINE}\n{s.guide}\n")
    print(f"Current: {show_value(s.key, current)}" + (f"   Default: {s.default}" if s.default else ""))
    new = ask("New value (Enter = keep, '-' = clear/default): ")
    if not new:
        return
    if new == "-":
        new = s.default
    if err := check_value(s, new):
        print(f"Not saved: {err}")
        pause()
        return
    values[s.key] = new
    save_values(values)
    print("Saved." + (" Log in to Spotify again (option 3) for this to take effect."
                      if s.key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_REDIRECT_URI") else ""))
    if s.key not in ("SPOTIFY_CLIENT_ID", "SPOTIFY_REDIRECT_URI"):
        print("Restart the bot for changes to apply.")


# ---------------- spotify login ----------------
def spotify_login() -> None:
    values = read_values()
    missing = [e for e in validate(values) if "Client" in e]
    if missing:
        print("\nSet your Spotify Client ID and secret first (option 2).")
        pause()
        return
    try:
        interactive_login(load_config())
    except Exception as e:
        print(f"\nLogin failed: {e}")
        print("If it says invalid_grant, the code expired. Try again and paste faster.")
        print("If it says INVALID_CLIENT or redirect mismatch, check Client ID / Redirect URI.")
    pause()


# ---------------- audio check ----------------
def audio_check() -> None:
    values = read_values()
    print(f"\n{LINE}\nAUDIO CHECK\n{LINE}")

    ffmpeg = audio.resolve_ffmpeg(values.get("FFMPEG_PATH", "ffmpeg"))
    if not ffmpeg:
        print("FFmpeg not found. Install it with:  winget install Gyan.FFmpeg")
        print("then close and reopen this window and run the check again.")
        pause()
        return
    print(f"FFmpeg: {ffmpeg}")
    if ffmpeg != values.get("FFMPEG_PATH"):
        values["FFMPEG_PATH"] = ffmpeg
        save_values(values)
        print("  (saved this path to your settings)")

    try:
        devices = audio.list_audio_devices(ffmpeg)
    except Exception as e:
        print(f"Couldn't list devices: {e}")
        pause()
        return
    current = values.get("AUDIO_DEVICE", "")
    print("\nAudio devices the bot can stream from:")
    for i, d in enumerate(devices, 1):
        print(f"  {i}) {d}{'   <- current' if d == current else ''}")
    if not devices:
        print("  (none found)")
    if current and current not in devices:
        print(f"\nYour setting '{current}' isn't in the list.")
        if not any("CABLE" in d for d in devices):
            print("No VB-CABLE found: install it from vb-audio.com/Cable and restart your PC.")
    pick = ask("\nPick a number to use it, or Enter to keep the current one: ")
    if pick.isdigit() and 1 <= int(pick) <= len(devices):
        values["AUDIO_DEVICE"] = current = devices[int(pick) - 1]
        save_values(values)
        print(f"Saved: {current}")

    if ask("\nTest the volume now? Play a song in Spotify first. (y/n): ").lower() == "y":
        print("Listening for 5 seconds...")
        mean, peak, raw = audio.measure_level(ffmpeg, current)
        if mean is None:
            print("Couldn't read that device. FFmpeg said:")
            print("\n".join(raw.strip().splitlines()[-5:]))
        elif peak == float("-inf") or (peak is not None and peak < -60):
            print(f"Silence (max {peak} dB). Set Spotify's output to CABLE Input in")
            print("Settings > System > Sound > Volume mixer, and make sure it's playing.")
        else:
            print(f"Average {mean} dB, peak {peak} dB.")
            if peak is not None and peak < -15:
                print("Quiet. Raise Volume boost (option 2) to about 1.5-2.")
            elif peak is not None and peak > -0.5 and float(values.get("AUDIO_GAIN") or 1) > 1:
                print("Already at full volume, so a boost above 1 will distort. Set Volume boost to 1.")
            else:
                print("Looks good.")
    pause()


# ---------------- main ----------------
def main() -> None:
    os.chdir(ROOT)
    if "--start" in sys.argv:
        start_bot()
        return
    actions = {"1": start_bot, "2": settings_menu, "3": spotify_login, "4": audio_check,
               "5": lambda: (print(SETUP_GUIDE), pause())}
    while True:
        print(f"\n{LINE}\nSPOTIFY JAM BOT  v{__version__}\n{LINE}")
        for line in status_lines(read_values()):
            print("  " + line)
        print("\n  1) Start the bot\n  2) Change settings\n  3) Log in to Spotify"
              "\n  4) Audio check (FFmpeg, devices, volume)\n  5) Setup guide\n  0) Quit")
        choice = ask("\nChoose: ")
        if choice == "0":
            return
        if action := actions.get(choice):
            action()
        else:
            print("Not an option.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
