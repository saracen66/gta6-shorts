"""Word-level speech transcription with faster-whisper (optional dependency)."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("gta6clips.transcribe")

Word = tuple[float, float, str]  # (start, end, text)


@lru_cache(maxsize=1)
def _model(name: str):
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="auto", compute_type="int8")


def transcribe(path: Path, model: str = "small") -> list[Word]:
    """Return words with timestamps relative to the file start. [] if unavailable."""
    try:
        m = _model(model)
    except Exception as e:
        log.warning("faster-whisper unavailable (%s); no transcript/captions", e)
        return []
    try:
        # decode with ffmpeg ourselves: faster-whisper's PyAV path breaks on some av versions
        import subprocess

        import numpy as np
        pcm = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-vn",
                              "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                             capture_output=True, timeout=900, check=True).stdout
        audio = np.frombuffer(pcm, dtype=np.float32)
        segments, _ = m.transcribe(audio, word_timestamps=True, vad_filter=True)
        words: list[Word] = []
        for seg in segments:
            for w in seg.words or []:
                text = w.word.strip()
                if text:
                    words.append((float(w.start), float(w.end), text))
        return words
    except Exception as e:
        log.warning("transcription failed for %s: %s", path.name, e)
        return []


def as_text(words: list[Word], every: float = 5.0) -> str:
    """Compact transcript with a [mm:ss] stamp every few seconds, for the judge prompt."""
    if not words:
        return "(no speech detected)"
    out, last = [], -every
    for s, _, w in words:
        if s - last >= every:
            out.append(f"\n[{int(s // 60):02d}:{s % 60:04.1f}] ")
            last = s
        out.append(w + " ")
    return "".join(out).strip()
