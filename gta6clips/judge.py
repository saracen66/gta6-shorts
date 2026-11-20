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


class JudgeUnavailable(RuntimeError):
    """Rate limit / usage limit / auth problem: stop judging for this cycle."""


# Models with server-side refusal fallback (Haiku has none).
_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")


def _header(c: Candidate, clip_len: float) -> str:
    why = {k: v for k, v in c.meta.items() if v is not None}
    return (f"Platform: {c.platform}\nStreamer: {c.streamer}\nStream/clip title: {c.title}\n"
            f"Source: {c.source}  signals: {why}\nClip length: {clip_len:.1f}s")


def _clamp(v: Verdict, clip_len: float) -> Verdict:
    v.trim_start = max(0.0, min(v.trim_start, clip_len))
    v.trim_end = max(v.trim_start, min(v.trim_end, clip_len))
    return v


def judge(c: Candidate, frames: list[tuple[float, Path]], transcript: str, clip_len: float,
          jc: dict) -> Verdict | None:
    provider = jc.get("provider", "api")
    if provider == "claude-code":
        return judge_claude_code(c, frames, transcript, clip_len, jc)
    return judge_api(c, frames, transcript, clip_len, jc)


def judge_api(c: Candidate, frames: list[tuple[float, Path]], transcript: str, clip_len: float,
              jc: dict) -> Verdict | None:
    """Anthropic API (needs ANTHROPIC_API_KEY or `ant auth login`). Pay per token."""
    model, effort = jc.get("model", "claude-haiku-5-5"), jc.get("effort", "medium")
    client = anthropic.Anthropic(max_retries=3)
    content: list[dict] = [{"type": "text", "text": _header(c, clip_len) + "\n\nFrames:"}]
    for t, f in frames:
        content.append({"type": "text", "text": f"t={t:.1f}s"})
        content.append(_img(f))
    content.append({"type": "text", "text": f"Transcript:\n{transcript}\n\nJudge this clip."})
    kwargs = dict(model=model, max_tokens=16000, system=SYSTEM,
                  messages=[{"role": "user", "content": content}], output_format=Verdict,
                  output_config={"effort": effort})
    try:
        if model.startswith(_FALLBACK_MODELS):
            resp = client.beta.messages.parse(**kwargs, betas=["server-side-fallback-2026-07-01"],
                                              fallbacks="default")
        else:
            resp = client.messages.parse(**kwargs)
    except (anthropic.RateLimitError, anthropic.AuthenticationError,
            anthropic.PermissionDeniedError) as e:
        raise JudgeUnavailable(str(e)) from e
    except anthropic.APIStatusError as e:
        log.error("judge API error %s for %s: %s", e.status_code, c.id, e.message)
        return None
    if resp.stop_reason == "refusal":
        log.info("judge refused %s (%s)", c.id, getattr(resp.stop_details, "category", None))
        return None
    v = resp.parsed_output
    return _clamp(v, clip_len) if v else None


def judge_claude_code(c: Candidate, frames: list[tuple[float, Path]], transcript: str,
                      clip_len: float, jc: dict) -> Verdict | None:
    """Judge through the Claude Code CLI (`claude -p`), using the logged-in Claude subscription.

    Counts against the plan's usage limits instead of API billing. Good for testing.
    """
    import json
    import shutil
    import subprocess

    exe = shutil.which("claude")
    if not exe:
        raise JudgeUnavailable("`claude` CLI not found on PATH")
    frame_dir = frames[0][1].parent if frames else Path.cwd()
    lines = [_header(c, clip_len), "", "Frames (read each image file with the Read tool):"]
    lines += [f"t={t:.1f}s: {f.resolve()}" for t, f in frames]
    lines += ["", f"Transcript:\n{transcript}", "",
              "Look at every frame, then judge this clip. Answer only with the structured output."]
    cmd = [exe, "-p", "--output-format", "json",
           "--json-schema", json.dumps(Verdict.model_json_schema()),
           "--system-prompt", SYSTEM,
           "--allowedTools", "Read", "--add-dir", str(frame_dir.resolve()),
           "--setting-sources", "project", "--strict-mcp-config", "--no-session-persistence",
           "--max-turns", "12"]
    if jc.get("model"):
        cmd += ["--model", jc["model"]]
    try:
        p = subprocess.run(cmd, input="\n".join(lines), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600, cwd=frame_dir)
    except subprocess.TimeoutExpired:
        log.error("claude -p timed out for %s", c.id)
        return None
    try:
        out = json.loads(p.stdout)
    except ValueError:
        log.error("claude -p bad output for %s: %s", c.id, (p.stderr or p.stdout)[-500:])
        return None
    if out.get("is_error"):
        msg = str(out.get("result", ""))
        if any(k in msg.lower() for k in ("limit", "authenticate", "login", "oauth", "credit")):
            raise JudgeUnavailable(msg)
        log.error("claude -p error for %s: %s", c.id, msg[:300])
        return None
    data = out.get("structured_output")
    if data is None:  # older CLI: JSON in the text result
        try:
            data = json.loads(out.get("result", ""))
        except ValueError:
            log.error("claude -p returned no structured output for %s", c.id)
            return None
    try:
        return _clamp(Verdict.model_validate(data), clip_len)
    except Exception as e:
        log.error("claude -p verdict invalid for %s: %s", c.id, e)
        return None
