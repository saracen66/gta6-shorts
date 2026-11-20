# gta6-shorts

An agent skill and standalone pipeline that runs unattended. It finds the best GTA VI stream moments on
**Twitch, Kick and YouTube**, has **Claude** judge them, renders captioned vertical shorts with streamer credit,
and posts them to **YouTube Shorts, TikTok, Instagram Reels and Facebook Reels**.

```
discover (Twitch clips / Kick clips / YouTube most-replayed / live chat+audio spikes)
   -> transcribe (faster-whisper) + sample frames
   -> Claude verdict (keep? score, trim, hook, title, caption, hashtags, safety)
   -> render 1080x1920 (blur fill, zoomed gameplay, captions, credit)
   -> publish via official APIs (daily caps + spacing per platform)
```

## Install as a skill

The repo root is a standard Agent Skill (`SKILL.md`). It works with any agent that loads skills from a folder.

```bash
# Claude Code
git clone https://github.com/<owner>/gta6-shorts ~/.claude/skills/gta6-shorts
# Codex / other agents that read ~/.agents/skills
git clone https://github.com/<owner>/gta6-shorts ~/.agents/skills/gta6-shorts
# or with the skills CLI
npx skills add <owner>/gta6-shorts
```

Then ask your agent: *"set up gta6-shorts"*. It follows `SKILL.md`.

## Use without an agent

```bash
bash scripts/setup.sh              # Windows: powershell -File scripts\setup.ps1
# fill in ~/gta6-shorts/.env  (see references/platform-setup.md)
scripts/gta6.sh auth youtube
scripts/gta6.sh auth tiktok
scripts/gta6.sh doctor
scripts/gta6.sh once               # one cycle
bash scripts/schedule_cron.sh 30   # Windows: powershell -File scripts\schedule_windows.ps1 -Minutes 30
```

The workspace (config, keys, tokens, videos, DB, logs) is `$GTA6_SHORTS_HOME`, default `~/gta6-shorts`.
It is separate from the code, so updating the skill (`git pull`) never touches your data.

## Requirements

Python 3.10+, ffmpeg on PATH, an Anthropic API key, and keys for each platform you enable.

## Rights

Streams belong to their streamers. GTA VI belongs to Rockstar Games. The pipeline credits the streamer, links
the original stream and adds a disclaimer to every post. That is courtesy, **not** a license: copyright
claims and strikes can still happen. Prefer streamers who allow clipping (`streamers.mode: allowlist`), and act
fast on takedowns with `gta6 block platform:streamer`. You are responsible for what you post.

## Tests

```bash
.venv/bin/python -m pytest tests
```
