"""ffmpeg / yt-dlp helpers."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("gta6clips.media")

def _python() -> str:
    import sys
    return sys.executable


def run(cmd: list[str], timeout: int = 1800, cwd: Path | None = None) -> subprocess.CompletedProcess:
    log.debug("run: %s", " ".join(map(str, cmd)))
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, cwd=cwd)
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "")[-1500:]
        raise RuntimeError(f"{Path(cmd[0]).name} failed ({p.returncode}): {tail}")
    return p


def ytdlp(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return run([_python(), "-m", "yt_dlp", "--no-warnings", "--no-progress", *args], timeout=timeout)


def info(url: str) -> dict:
    """Metadata (incl. YouTube 'heatmap' most-replayed data) without downloading."""
    p = ytdlp(["-J", "--skip-download", url], timeout=180)
    return json.loads(p.stdout)


def download(url: str, out: Path, start: float | None = None, end: float | None = None,
             max_height: int = 1080) -> Path:
    """Download whole media or just [start, end] seconds. Returns mp4 path."""
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-f", f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "--merge-output-format", "mp4", "-o", str(out.with_suffix(".%(ext)s")),
        "--no-playlist",
    ]
    if start is not None and end is not None:
        args += ["--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts"]
    ytdlp([*args, url])
    if out.with_suffix(".mp4").exists():
        return out.with_suffix(".mp4")
    found = sorted(out.parent.glob(out.stem + ".*"))
    found = [f for f in found if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not found:
        raise RuntimeError(f"download produced no file for {url}")
    return found[0]


def stream_url(url: str) -> str:
    p = ytdlp(["-g", "-f", "b[height<=1080]/best", url], timeout=120)
    return p.stdout.strip().splitlines()[0]


def record_live(url: str, out: Path, seconds: int) -> Path:
    src = stream_url(url)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-t", str(seconds), "-c", "copy",
         str(out)], timeout=seconds + 300)
    return out


def duration(path: Path) -> float:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
             "default=nw=1:nk=1", str(path)], timeout=60)
    return float(p.stdout.strip() or 0)


def has_audio(path: Path) -> bool:
    p = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
             "-of", "csv=p=0", str(path)], timeout=60)
    return bool(p.stdout.strip())


def frames(path: Path, out_dir: Path, n: int, start: float = 0.0,
           end: float | None = None) -> list[tuple[float, Path]]:
    """Grab n evenly spaced JPEG frames. Returns [(time, path)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    end = end if end is not None else duration(path)
    span = max(end - start, 0.1)
    result = []
    for i in range(n):
        t = start + span * (i + 0.5) / n
        f = out_dir / f"frame_{i:02d}.jpg"
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
             "-frames:v", "1", "-vf", "scale=768:-2", "-q:v", "4", str(f)], timeout=60)
        result.append((t, f))
    return result


_M_RE = re.compile(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+|-inf)")


def loudness_series(path: Path) -> list[tuple[float, float]]:
    """Momentary loudness (LUFS) every ~0.1s via ebur128. [(t, lufs)]."""
    p = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=none",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600)
    series = []
    for m in _M_RE.finditer(p.stderr):
        v = m.group(2)
        series.append((float(m.group(1)), -70.0 if v == "-inf" else float(v)))
    return series
