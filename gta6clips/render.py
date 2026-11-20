"""Render a 9:16 short: blurred fill background, zoomed gameplay, hook title, word captions, credit."""
from __future__ import annotations

from pathlib import Path

from . import media
from .transcribe import Word

W, H = 1080, 1920


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


PROFANITY = ("fuck", "shit", "bitch", "cunt", "nigg", "fag", "retard", "dick", "pussy", "whore",
             "slut", "bastard", "motherf", "asshole")


def censor(word: str) -> str:
    """'fucking' -> 'f***ing' style mask; keeps captions brand-safe for the algorithms."""
    low = word.lower()
    for bad in PROFANITY:
        i = low.find(bad)
        if i >= 0:
            return word[: i + 1] + "*" * (len(bad) - 1) + word[i + len(bad):]
    return word


def caption_chunks(words: list[Word], max_words: int = 3, max_gap: float = 0.6,
                   max_len: float = 1.6) -> list[tuple[float, float, str]]:
    """Group words into short punchy on-screen chunks."""
    chunks: list[tuple[float, float, str]] = []
    cur: list[Word] = []
    for w in words:
        if cur and (len(cur) >= max_words or w[0] - cur[-1][1] > max_gap or w[1] - cur[0][0] > max_len):
            chunks.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
            cur = []
        cur.append(w)
    if cur:
        chunks.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
    # close tiny gaps so captions don't flicker
    fixed = []
    for i, (s, e, t) in enumerate(chunks):
        if i + 1 < len(chunks) and chunks[i + 1][0] - e < 0.25:
            e = chunks[i + 1][0]
        fixed.append((s, max(e, s + 0.3), t))
    return fixed


def build_ass(duration: float, hook: str, credit: str, watermark: str, words: list[Word],
              font: str = "Arial Black", video_top: int = 600, video_bottom: int = 1320) -> str:
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Hook,{font},78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,3,8,60,60,{video_top - 260},1",
        f"Style: Credit,Arial,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,8,60,60,{video_top - 70},1",
        f"Style: Cap,{font},86,&H0000F0FF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,3,8,50,50,{video_bottom + 50},1",
        "Style: Mark,Arial,34,&H80FFFFFF,&H00FFFFFF,&H80000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,9,40,40,60,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    end = ass_time(duration)
    if hook:
        lines.append(f"Dialogue: 1,0:00:00.00,{end},Hook,,0,0,0,,{{\\fad(150,0)}}{ass_escape(hook.upper())}")
    if credit:
        lines.append(f"Dialogue: 1,0:00:00.00,{end},Credit,,0,0,0,,{ass_escape(credit)}")
    if watermark:
        lines.append(f"Dialogue: 1,0:00:00.00,{end},Mark,,0,0,0,,{ass_escape(watermark)}")
    for s, e, text in caption_chunks(words):
        if s >= duration:
            break
        lines.append(f"Dialogue: 2,{ass_time(s)},{ass_time(min(e, duration))},Cap,,0,0,0,,"
                     f"{{\\fscx90\\fscy90\\t(0,80,\\fscx105\\fscy105)\\t(80,160,\\fscx100\\fscy100)}}"
                     f"{ass_escape(text.upper())}")
    return "\n".join(lines) + "\n"


def shift_words(words: list[Word], start: float, end: float) -> list[Word]:
    return [(s - start, e - start, t) for s, e, t in words if s >= start and e <= end + 0.3]


def render(src: Path, out: Path, start: float, end: float, words: list[Word], hook: str,
           credit: str, cfg: dict) -> Path:
    rc = cfg["render"]
    work = out.parent / (out.stem + "_work")
    work.mkdir(parents=True, exist_ok=True)
    dur = end - start
    zoom = float(rc.get("fg_zoom", 1.15))
    fg_w = int(W * zoom) // 2 * 2
    fg_h = int(fg_w * 9 / 16) // 2 * 2
    top = (H - fg_h) // 2
    cap_words = shift_words(words, start, end) if rc.get("captions", True) else []
    if rc.get("censor_captions", True):
        cap_words = [(s, e, censor(w)) for s, e, w in cap_words]
    ass = build_ass(dur, hook, credit, rc.get("watermark", ""), cap_words,
                    rc.get("font", "Arial Black"), video_top=top, video_bottom=top + fg_h)
    (work / "subs.ass").write_text(ass, encoding="utf-8")
    vf = (
        f"[0:v]split=2[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:3,"
        f"eq=brightness=-0.12[bg];"
        f"[b]scale={fg_w}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h}[fg];"
        f"[bg][fg]overlay=0:{top},subtitles=subs.ass,fps=30,format=yuv420p[v]"
    )
    audio = media.has_audio(src)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}",
           "-i", str(src.resolve())]
    if audio:
        cmd += ["-filter_complex", vf + ";[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[au]",
                "-map", "[v]", "-map", "[au]"]
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.2f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-filter_complex", vf, "-map", "[v]", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest",
            str(out.resolve())]
    media.run(cmd, timeout=1800, cwd=work)
    return out
