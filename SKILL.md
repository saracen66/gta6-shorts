---
name: gta6-shorts
description: Autonomous GTA VI (GTA 6) stream clipper. Finds the best, funniest, WTF and viral moments in GTA VI streams on Twitch, Kick and YouTube, has Claude judge them, renders captioned 9:16 shorts with streamer credit, and posts them to YouTube Shorts, TikTok, Instagram Reels and Facebook Reels on a schedule. Use when the user wants to set up, run, schedule, monitor, tune or troubleshoot GTA VI clip/shorts automation, add or remove streamers, handle a takedown request, or check what was posted.
---

# GTA VI Shorts

Pipeline that runs unattended. Each cycle:

1. **Discover** candidates
   - Twitch: viewer-made clips in the GTA VI category (sorted by views).
   - Kick: clips from the biggest live GTA VI channels.
   - YouTube: recent GTA VI streams/VODs, using the "most replayed" heatmap to find peaks.
   - Live recording (optional): records chunks of live streams and finds chat-spam and loudness spikes.
2. **Judge**: transcribes speech (faster-whisper), samples frames, and has Claude score the clip (1-10). Claude also picks the trim points and writes the hook, title, caption and hashtags. Clips that aren't GTA VI, are unsafe, or score low are rejected.
3. **Render** a 1080x1920 short: blurred background, zoomed gameplay, hook title, word-by-word captions, streamer credit and optional watermark.
4. **Publish** through the official APIs, respecting per-platform daily caps and spacing. Every description credits the streamer with a link, links the source stream, and includes the disclaimer.

Code lives in this skill folder. User data (config, keys, tokens, DB, videos, logs) lives in the **workspace**: `$GTA6_SHORTS_HOME`, default `~/gta6-shorts`.

## Running commands

All commands go through the wrapper. It uses the skill's own venv:

- Windows: `powershell -File <skill>/scripts/gta6.ps1 <command>`
- macOS/Linux: `bash <skill>/scripts/gta6.sh <command>`

| Command | What it does |
|---|---|
| `init` | Create workspace `config.yaml` and `.env` from the templates |
| `doctor` | Check ffmpeg, keys and tokens; exit 1 and print fixes if anything is missing |
| `auth youtube` / `auth tiktok` | One-time browser OAuth; token saved in workspace `secrets/` |
| `once` | One full cycle: discover, judge, render, publish, clean up (use from schedulers) |
| `run [--minutes N]` | Loop forever |
| `discover` / `process` / `publish` | Run a single stage |
| `status` | Counts by state, plus the last 20 posts with errors |
| `block platform:streamer` | Takedown: blocklist the streamer, cancel queued posts, list live posts to delete by hand |
| `test-url <url> [--start S --end E]` | Judge + render one clip/video URL (never posts). Best first test |
| `test-render <video> [--start S --end E --hook TEXT]` | Run a local file through the edit to check the look |

## First-time setup (do in order)

1. Check that Python 3.10+ and ffmpeg are installed.
2. Run `scripts/setup.ps1` (Windows) or `scripts/setup.sh`. This creates the venv, installs `requirements.txt`, and runs `init`.
3. Have the user fill in workspace `.env`. Only the sources they use are needed (YouTube search works without a key). Per-platform steps are in [references/platform-setup.md](references/platform-setup.md). Never ask the user to paste secrets into chat. They edit `.env` themselves.
4. Run `auth youtube` and `auth tiktok`. Each opens a browser for the user to log in.
5. Run `doctor` until it is clean. Disable any platform the user doesn't use in `config.yaml` (`platforms.<name>.enabled: false`).
6. Do a dry run with publishing off. Set every `platforms.*.enabled: false`, then run `once`. Show the user the renders in `<workspace>/data/out/`: each `.mp4` has a `.json` with Claude's verdict. Turn the platforms back on after the user approves the look.
7. Schedule it:
   - Windows: `scripts/schedule_windows.ps1 -Minutes 30`
   - Linux/macOS: `scripts/schedule_cron.sh 30`
   - Remove the schedule with `-Remove` or `remove`.

## Operating playbook (for agents)

- **"How is it doing?"**: run `status`, then read the tail of `<workspace>/logs/pipeline.log`. Report posts per platform, rejections and their reasons, and any errors.
- **Clips are weak**: raise `judge.min_score`, raise `sources.twitch_clips.min_views`, or lower `youtube.min_heat`. To get clips from more streams, raise `max_per_cycle`.
- **Judge provider**: `judge.provider: api` (default, Haiku 5.5, a fraction of a cent per clip) needs `ANTHROPIC_API_KEY`. `judge.provider: claude-code` uses the logged-in Claude subscription through `claude -p`. No API bill, but it counts against the plan's usage limits, so use it for testing. If `claude auth status` shows `loggedIn: false`, the user must run `claude auth login`.
- **Too costly**: lower `judge.max_per_cycle` or `judge.frames`. Use `judge.effort: low`. Only change `judge.model` if the user asks for a different model.
- **Takedown or complaint from a streamer**: run `block platform:streamer` right away. Then tell the user exactly which posted IDs still need to be deleted by hand on each platform.
- **Only clip approved streamers**: set `streamers.mode: allowlist` and list entries like `twitch:name`, `kick:slug`, `youtube:@handle`.
- **Upload errors**:
  - TikTok `SELF_ONLY` or privacy errors: the app is not audited yet. Use `tiktok.mode: inbox` (posts go to drafts) until TikTok approves the app.
  - YouTube `quotaExceeded`: lower `youtube.max_per_day`.
  - Meta `(#10)` or `(#200)`: the token is missing permissions (see the reference).
- Never raise posting caps above platform limits. Never remove the credit line or the disclaimer. Never post a clip from a blocklisted streamer.

## Rights warning (tell the user once during setup)

Stream footage belongs to the streamer, and the game to Rockstar. Credit and a disclaimer are good practice, but they are **not** a license. Copyright claims and strikes can still happen. Fewer strikes come from:

- streamers who openly allow clipping (or the allowlist mode),
- quick takedowns with `block`,
- the added edit (hook, captions, trim).

The user is responsible for what gets posted.

## Files

- `gta6clips/`: the pipeline (`discover`, `signals`, `live`, `transcribe`, `judge`, `render`, `publish`, `db`, `__main__`)
- `config.example.yaml`: every setting, with comments
- `.env.example`: every key, and where to get it
- `references/platform-setup.md`: step-by-step credentials for each platform
- `tests/`: `python -m pytest tests`
