# Spotify Jam Bot

A Discord bot that runs your Spotify Jam with your friends:

- **Streams the music into your voice call**, so friends don't need Spotify.
- **Queues songs from Discord** with `/play`, which suggests songs as you type.
- **Posts what's playing**, who requested it, or which playlist it's shuffling from. The previous now-playing post is deleted, so the channel only shows the current song plus what's been queued.
- **Spots songs queued in the Spotify app** and adds them to `/queue`.
- **Changes the station** (`/station`): what it shuffles when nobody's queued anything.
- **Has a settings menu** with a step-by-step guide for every setting.

## How it works

- **Queue control:** the bot logs in to the host's Spotify (Premium) and controls playback and the queue through the Spotify Web API. Spotify has no API for Jams, so the bot controls the host's playback, which is what the Jam plays.
- **Voice audio:** Spotify won't let a bot stream its audio directly. Instead, the host's Spotify app plays into a virtual audio cable (VB-CABLE), and the bot streams that cable into the voice channel with FFmpeg.

## Requirements

- Windows 10/11 (voice capture uses DirectShow)
- **Python 3.13** (3.14+ won't work yet: the voice encryption library this needs has no builds for it)
- Spotify **Premium** (host)
- [FFmpeg](https://ffmpeg.org) and [VB-CABLE](https://vb-audio.com/Cable/)

## Install

```powershell
winget install Python.Python.3.13
winget install Gyan.FFmpeg
```

Install VB-CABLE: download from vb-audio.com/Cable, extract, right-click `VBCABLE_Setup_x64.exe` → **Run as administrator** → **Install Driver**, then restart.

Then, in the project folder:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

If PowerShell says running scripts is disabled: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Run

Double-click **`start.bat`**, or:

```powershell
.venv\Scripts\activate
python run.py
```

The menu:

```
  1) Start the bot
  2) Change settings
  3) Log in to Spotify
  4) Audio check (FFmpeg, devices, volume)
  5) Setup guide
```

The top of the menu shows what still needs fixing. Every setting in **2) Change settings** comes with instructions for where to get its value. **4) Audio check** finds FFmpeg, lists your audio devices so you can pick one, and measures the volume. `python run.py --start` skips the menu.

## Settings

All settings live in `.env`. The menu edits this file for you.

| Setting | What it's for |
|---|---|
| Bot token* | Discord bot token (Developer Portal → Bot → Reset Token) |
| Server ID | Makes slash commands show up instantly |
| Your user ID (host)* | Host-only powers: `/setjam`, `/endjam`, instant skip, `/join` without a Jam |
| Now-playing channel ID | Where now-playing and "Queued in Spotify" posts go |
| Skip votes needed | Votes for `/skip` (default 2) |
| Client ID* / Client secret* | From your app at developer.spotify.com/dashboard |
| Redirect URI | Must match the Spotify app (default `http://127.0.0.1:8888/callback`) |
| Audio device | What gets streamed into voice (default VB-CABLE's output) |
| Volume boost | 1 = normal; raise if quiet, lower if distorted |
| FFmpeg path | Found automatically by Audio check |
| Advanced | Capture channels, capture backend, Spotify check interval |

\* required

## Commands

| Command | Who | What |
|---|---|---|
| `/setjam <link>` | host | Start the Jam (Spotify → Start a Jam → Invite → Copy link) |
| `/endjam` | host | End the Jam; the bot leaves voice |
| `/jam` | anyone | Jam link and current song |
| `/join` | anyone during a Jam, host anytime | Stream the music into your voice channel. If the host runs it with no Jam, it streams their Spotify directly |
| `/leave` | anyone | Bot leaves voice |
| `/play <song>` | anyone | Type a song and pick from the suggestions, or paste a Spotify track link |
| `/queue` | anyone | Songs people queued, and who queued them |
| `/np` | anyone | Current song, progress, and where it came from |
| `/skip` | anyone | Vote to skip (host skips instantly) |
| `/pause` `/resume` | anyone | Playback control |
| `/station <source> [now]` | anyone during a session | Change the playlist/album/artist it shuffles when the queue is empty. It waits for queued songs to finish unless `now` is true |
| `/testtone` | anyone | 5-second beep to test voice audio |
| `/help` | anyone | Command list |

## Each session

1. Play something in Spotify. Make sure Spotify's output is **CABLE Input** (Windows Settings → System → Sound → Volume mixer).
2. Start a Jam → Invite → Copy link.
3. Start the bot, then in Discord run `/setjam <link>` and `/join`.
4. When you're done, run `/endjam`.

## Limits

- **Who queued a song:** Spotify's API doesn't say who queued a song in the Jam, so songs added in the Spotify app show as "queued in the Spotify app". Songs added with `/play` show the person's name.
- **Spotify-made mixes:** mixes like "House Mix" may show as "a playlist". Development-mode Spotify apps often can't read their names.
- **Search results:** search returns at most 10 results (a Spotify development-mode limit).
- **Spotify login:** it expires every few months. Use menu option 3 to log in again.

## Troubleshooting

- **Bot is in the call but nobody hears anything, even `/testtone`.** Check that davey is below 0.2: `pip show davey`. The menu warns you if it isn't. Fix it with `pip install "davey<0.2"`.
- **Nobody hears the music, but `/testtone` works.** Check that Spotify's output is CABLE Input, then run menu option 4 → volume test.
- **You can't hear Discord at all after installing VB-CABLE.** VB-CABLE took over your default devices. In Sound settings (`mmsys.cpl`), set your headset as both **Default Device** and **Default Communication Device**.
- **The music sounds distorted or "bass boosted".** Set Volume boost to 1.
- **"No active Spotify device".** Open Spotify and press play once.
- **Slash commands don't show up.** Set the Server ID, restart the bot, then press Ctrl+R in Discord.

## Project structure

```
run.py                 menu / launcher
start.bat              double-click launcher for Windows
jambot/
  bot.py               Discord commands, voice, now-playing posts
  spotify_service.py   Spotify login + API calls
  tracking.py          who queued what
  audio.py             FFmpeg capture, device list, volume test
  config.py            settings, setup guides, .env read/write
```

Local files that are **never** committed (see `.gitignore`): `.env` (tokens), `.spotify_cache` (Spotify login), `state.json`, `.venv/`.

## Push notes

**New repo:**

1. Create an empty repo named `spotify-jam-bot` at github.com/new. Leave "Add a README" unticked.
2. In the project folder, run:

```powershell
git init
git add .
git status        # check that .env, .spotify_cache, state.json and .venv are NOT listed
git commit -m "Spotify Jam Bot v2"
git branch -M main
git remote add origin https://github.com/<your-username>/spotify-jam-bot.git
git push -u origin main
```

**Existing repo** (the old single-file version): delete the old `bot.py` and `auth.py`, copy these files in, then run:

```powershell
git add -A
git status
git commit -m "v2: package layout, settings menu, /station, cleaner now-playing posts"
git push
```

If a token ever gets pushed, reset it right away: Discord Developer Portal → Reset Token, and Spotify Dashboard → Rotate client secret.
