"""Claude watches sampled frames + reads the transcript and decides if a moment is postable."""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .db import Candidate

log = logging.getLogger("gta6clips.judge")

SYSTEM = """You are the editor of a short-form channel that posts the best GTA VI (Grand Theft Auto VI) \
stream moments to YouTube Shorts, TikTok, Instagram Reels and Facebook Reels.

You get evenly spaced frames from a candidate clip (with timestamps), its speech transcript, and \
why it was flagged (viewer clip views, chat spike, "most replayed" heat, loudness spike).

Decide whether it is worth posting. Good clips have a clear payoff a scroller gets in the first \
2 seconds: funny fails, physics glitches, chaotic police chases, unexpected NPC behaviour, \
streamer screaming/raging, insane stunts, clutch skill, wholesome or shocking moments, big \
reveals. Reject: menus, loading screens, idle driving, talking with no visible payoff, non-GTA-VI \
footage, ads/sponsor reads, and anything with nudity, slurs, real personal info (doxxing), \
minors, or real-world violence.

Choose trim_start/trim_end (seconds, relative to clip start) that keep only the moment: start \
1-3s before the setup, end right after the reaction. Keep it 8-59 seconds.

Write like a real gaming clip page, not a brand: short hook_title for on-screen text \
(max 6 words, ALL CAPS ok, no hashtags), post_title (max 90 chars, curiosity-driven, may name \
the streamer), caption (1-2 sentences). No clickbait lies about what happens. 3-6 hashtags \
specific to the moment (the channel adds its own #GTA6 tags)."""


class Verdict(BaseModel):
    keep: bool
    category: Literal["funny", "wtf", "viral", "skill", "fail", "glitch", "rage", "wholesome",
                      "reveal", "other"]
    score: int = Field(description="1-10 virality/quality score")
    reason: str
    is_gta6_gameplay: bool
    safety_flags: list[str] = Field(description="empty if clean")
    trim_start: float
    trim_end: float
    hook_title: str
    post_title: str
    caption: str
    hashtags: list[str]


def _img(path: Path) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(path.read_bytes()).decode()}}


def judge(c: Candidate, frames: list[tuple[float, Path]], transcript: str, clip_len: float,
          model: str, effort: str) -> Verdict | None:
    client = anthropic.Anthropic(max_retries=3)
    why = {k: v for k, v in c.meta.items() if v is not None}
    content: list[dict] = [{"type": "text", "text": (
        f"Platform: {c.platform}\nStreamer: {c.streamer}\nStream/clip title: {c.title}\n"
        f"Source: {c.source}  signals: {why}\nClip length: {clip_len:.1f}s\n\nFrames:")}]
    for t, f in frames:
        content.append({"type": "text", "text": f"t={t:.1f}s"})
        content.append(_img(f))
    content.append({"type": "text", "text": f"Transcript:\n{transcript}\n\nJudge this clip."})
    try:
        resp = client.beta.messages.parse(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_format=Verdict,
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as e:
        log.warning("judge rate limited: %s", e)
        raise
    except anthropic.APIStatusError as e:
        log.error("judge API error %s for %s: %s", e.status_code, c.id, e.message)
        return None
    if resp.stop_reason == "refusal":
        log.info("judge refused %s (%s)", c.id, getattr(resp.stop_details, "category", None))
        return None
    v = resp.parsed_output
    if v is None:
        return None
    v.trim_start = max(0.0, min(v.trim_start, clip_len))
    v.trim_end = max(v.trim_start, min(v.trim_end, clip_len))
    return v
