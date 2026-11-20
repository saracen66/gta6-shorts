"""Record live GTA VI streams in chunks and turn chat/audio spikes into candidates."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from . import media
from .config import RAW
from .db import DB, Candidate
from .discover import live_targets
from .signals import audio_peaks, chat_peaks, live_windows

log = logging.getLogger("gta6clips.live")


def _collect_chat(url: str, seconds: int, sink: list[tuple[float, str]], t0: float) -> None:
    try:
        from chat_downloader import ChatDownloader
        chat = ChatDownloader().get_chat(url, timeout=seconds)
        for m in chat:
            ts = (m.get("timestamp") or 0) / 1e6
            sink.append(((ts - t0) if ts else time.time() - t0, m.get("message") or ""))
    except Exception as e:  # chat is a bonus signal, never fatal
        log.warning("chat capture failed for %s: %s", url, e)


def record_and_detect(cfg: dict, db: DB) -> int:
    lc = cfg["sources"]["live_record"]
    if not lc.get("enabled"):
        return 0
    seconds = int(lc.get("chunk_minutes", 10) * 60)
    jobs = []
    for platform, streamer, url in live_targets(cfg):
        out = RAW / f"live_{platform}_{streamer}_{int(time.time())}.mp4"
        chat: list[tuple[float, str]] = []
        t0 = time.time()
        th_chat = threading.Thread(target=_collect_chat, args=(url, seconds, chat, t0), daemon=True)
        th_rec = threading.Thread(target=_safe_record, args=(url, out, seconds), daemon=True)
        th_chat.start(); th_rec.start()
        jobs.append((platform, streamer, url, out, chat, t0, th_chat, th_rec))
    added = 0
    for platform, streamer, url, out, chat, t0, th_chat, th_rec in jobs:
        th_rec.join(seconds + 360)
        th_chat.join(30)
        if not out.exists() or out.stat().st_size < 1_000_000:
            log.warning("live %s/%s: no recording", platform, streamer)
            continue
        total = media.duration(out)
        audio = audio_peaks(media.loudness_series(out)) if media.has_audio(out) else []
        cpeaks = chat_peaks(chat)
        for start, end, score in live_windows(cpeaks, audio, total):
            cand = Candidate(
                id=f"live:{platform}:{streamer}:{int(t0 + start)}", source="live", platform=platform,
                url=str(out), source_url=url, streamer=streamer, streamer_url=url,
                title=f"{streamer} live", start=start, end=end, moment_ts=t0 + start,
                signal=score, meta={"chat_msgs": len(chat), "audio_peaks": len(audio)},
            )
            added += db.add_candidate(cand)
        log.info("live %s/%s: %d chat peaks, %d audio peaks", platform, streamer, len(cpeaks), len(audio))
    return added


def _safe_record(url: str, out: Path, seconds: int) -> None:
    try:
        media.record_live(url, out, seconds)
    except Exception as e:
        log.warning("record %s failed: %s", url, e)
