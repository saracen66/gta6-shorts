"""Candidate -> downloaded media -> transcript + frames -> Claude verdict -> rendered short -> post queue."""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from . import media
from .config import OUT, RAW, WORK
from .db import DB, Candidate
from .filters import streamer_allowed
from .judge import JudgeUnavailable, judge
from .render import render
from .transcribe import as_text, transcribe

log = logging.getLogger("gta6clips.process")


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s)[:80]


def fetch(c: Candidate) -> tuple[Path, float]:
    """Return (local file, offset of file start within the candidate's media)."""
    if Path(c.url).exists():  # live recording already on disk
        return Path(c.url), 0.0
    out = RAW / safe_name(c.id)
    if c.start is not None and c.end is not None:
        return media.download(c.url, out, c.start, c.end), c.start
    return media.download(c.url, out), 0.0


def credit_line(c: Candidate) -> str:
    return {"twitch": f"twitch.tv/{c.streamer}", "kick": f"kick.com/{c.streamer}"}.get(
        c.platform, f"YT: {c.streamer}")


def build_description(cfg: dict, c: Candidate, caption: str, tags: list[str]) -> str:
    pc = cfg["post"]
    all_tags = []
    for t in [*tags, *pc.get("hashtags", [])]:
        t = "#" + t.lstrip("#").replace(" ", "")
        if t.lower() not in {x.lower() for x in all_tags}:
            all_tags.append(t)
    return pc["description_template"].format(
        caption=caption, streamer=c.streamer, streamer_url=c.streamer_url,
        source_url=c.source_url, disclaimer=pc["disclaimer"].strip(),
        hashtags=" ".join(all_tags[:12]),
    ).strip()


def enabled_platforms(cfg: dict) -> list[str]:
    return [p for p, pc in cfg["platforms"].items() if pc.get("enabled")]


def process_one(cfg: dict, db: DB, c: Candidate) -> str:
    if not streamer_allowed(cfg, c.platform, c.streamer):
        db.set_status(c.id, "rejected", "streamer not allowed")
        return "rejected"
    rc, jc = cfg["render"], cfg["judge"]
    work = WORK / safe_name(c.id)
    try:
        src, _ = fetch(c)
        # live recordings: the window lives inside a long file
        win_start, win_end = (c.start or 0.0, c.end) if Path(c.url).exists() else (0.0, None)
        total = media.duration(src)
        win_end = min(win_end or total, total)
        clip_len = win_end - win_start
        if clip_len < rc["min_seconds"]:
            db.set_status(c.id, "rejected", f"too short ({clip_len:.1f}s)")
            return "rejected"

        if Path(c.url).exists():  # cut window out of the live recording first
            cut = RAW / f"{safe_name(c.id)}.mp4"
            media.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{win_start:.2f}", "-t",
                       f"{clip_len:.2f}", "-i", str(src), "-c", "copy", str(cut)])
            src, total, win_start, win_end = cut, media.duration(cut), 0.0, None
            win_end = total
            clip_len = total

        words = transcribe(src, rc.get("whisper_model", "small")) if media.has_audio(src) else []
        frames = media.frames(src, work / "frames", jc.get("frames", 6), win_start, win_end)
        verdict = judge(c, frames, as_text(words), clip_len, jc)
        if verdict is None:
            db.set_status(c.id, "failed", "no verdict")
            return "failed"
        vd = verdict.model_dump()
        reasons = []
        if not verdict.keep:
            reasons.append("judge: skip")
        if not verdict.is_gta6_gameplay:
            reasons.append("not GTA VI")
        if verdict.safety_flags:
            reasons.append("safety: " + ",".join(verdict.safety_flags))
        if verdict.score < jc["min_score"]:
            reasons.append(f"score {verdict.score}<{jc['min_score']}")
        if reasons:
            db.set_status(c.id, "rejected", "; ".join(reasons) + f" | {verdict.reason}"[:500])
            log.info("rejected %s: %s", c.id, reasons)
            return "rejected"

        start = verdict.trim_start
        end = min(verdict.trim_end, start + rc["max_seconds"])
        if end - start < rc["min_seconds"]:
            start, end = 0.0, min(clip_len, rc["max_seconds"])
        out = OUT / f"{safe_name(c.id)}.mp4"
        render(src, out, start, end, words, verdict.hook_title, credit_line(c), cfg)
        desc = build_description(cfg, c, verdict.caption, verdict.hashtags)
        vd["final_start"], vd["final_end"] = start, end
        db.add_clip(c.id, str(out), vd, desc, enabled_platforms(cfg))
        (OUT / f"{safe_name(c.id)}.json").write_text(
            json.dumps({"candidate": c.__dict__, "verdict": vd, "description": desc}, indent=2,
                       default=str), encoding="utf-8")
        log.info("rendered %s score=%d %s", c.id, verdict.score, verdict.post_title)
        return "rendered"
    except JudgeUnavailable:
        raise
    except Exception as e:
        log.exception("processing %s failed", c.id)
        db.set_status(c.id, "failed", str(e)[:500])
        return "failed"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def process_pending(cfg: dict, db: DB) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in db.pending_candidates(cfg["judge"]["max_per_cycle"]):
        try:
            r = process_one(cfg, db, c)
        except JudgeUnavailable as e:
            log.warning("judge unavailable, stopping this cycle: %s", e)
            break
        counts[r] = counts.get(r, 0) + 1
    return counts
