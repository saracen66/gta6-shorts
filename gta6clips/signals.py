"""Pure heuristics that turn raw signals into candidate time windows."""
from __future__ import annotations

import statistics
from typing import Iterable

HYPE_WORDS = (
    "lul", "lmao", "lmfao", "kekw", "omegalul", "icant", "wtf", "what", "no way", "clip",
    "clip it", "clip that", "omg", "pog", "poggers", "holy", "bro", "😂", "🤣", "💀", "?????",
)


def merge_windows(wins: list[tuple[float, float, float]], gap: float = 5.0) -> list[tuple[float, float, float]]:
    """Merge overlapping/near (start, end, score) windows, keeping max score."""
    out: list[list[float]] = []
    for s, e, sc in sorted(wins):
        if out and s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
            out[-1][2] = max(out[-1][2], sc)
        else:
            out.append([s, e, sc])
    return [tuple(w) for w in out]  # type: ignore[misc]


def clamp_window(start: float, end: float, total: float, max_len: float) -> tuple[float, float]:
    start = max(0.0, start)
    if total > 0:
        end = min(end, total)
    if end - start > max_len:
        mid = (start + end) / 2
        start, end = mid - max_len / 2, mid + max_len / 2
    return round(start, 2), round(end, 2)


def heatmap_windows(heatmap: list[dict], min_heat: float, limit: int, total: float,
                    pad_before: float = 20.0, pad_after: float = 15.0,
                    max_len: float = 75.0) -> list[tuple[float, float, float]]:
    """YouTube 'most replayed' segments -> top windows [(start, end, heat)].

    Skips the first 2% of the video (intro replays are not moments).
    """
    skip = total * 0.02
    peaks = [(h["start_time"] - pad_before, h["end_time"] + pad_after, float(h["value"]))
             for h in heatmap if float(h.get("value", 0)) >= min_heat and h["start_time"] > skip]
    merged = merge_windows(peaks)
    merged.sort(key=lambda w: w[2], reverse=True)
    return [(*clamp_window(s, e, total, max_len), sc) for s, e, sc in merged[:limit]]


def bucketize(times: Iterable[float], bucket: float) -> dict[int, int]:
    counts: dict[int, int] = {}
    for t in times:
        b = int(t // bucket)
        counts[b] = counts.get(b, 0) + 1
    return counts


def chat_peaks(messages: list[tuple[float, str]], bucket: float = 5.0, z: float = 2.5,
               min_msgs: int = 8) -> list[tuple[float, float]]:
    """Chat spikes -> [(time, strength)]. messages = [(seconds, text)].

    Score per bucket = message count + 1.5x hype-word hits; spike = z-score above mean.
    """
    if not messages:
        return []
    weights: dict[int, float] = {}
    for t, text in messages:
        b = int(t // bucket)
        low = text.lower()
        hype = sum(1 for w in HYPE_WORDS if w in low)
        weights[b] = weights.get(b, 0.0) + 1.0 + 1.5 * min(hype, 2)
    last = max(weights)
    series = [weights.get(i, 0.0) for i in range(last + 1)]
    if len(series) < 6:
        return []
    mean = statistics.fmean(series)
    sd = statistics.pstdev(series) or 1.0
    peaks = []
    for i, v in enumerate(series):
        if v < min_msgs or (v - mean) / sd < z:
            continue
        if v >= max(series[max(0, i - 2): i + 3]):  # local max
            peaks.append((i * bucket + bucket / 2, (v - mean) / sd))
    return peaks


def audio_peaks(series: list[tuple[float, float]], window: float = 2.0, db_over: float = 9.0,
                min_gap: float = 20.0) -> list[tuple[float, float]]:
    """Loudness spikes (screams, explosions) -> [(time, dB over median)]."""
    if len(series) < 50:
        return []
    step = series[1][0] - series[0][0] or 0.1
    k = max(1, int(window / step))
    vals = [v for _, v in series]
    smooth = [statistics.fmean(vals[max(0, i - k): i + 1]) for i in range(len(vals))]
    med = statistics.median(smooth)
    peaks: list[tuple[float, float]] = []
    for i, v in enumerate(smooth):
        over = v - med
        if over < db_over:
            continue
        t = series[i][0]
        if peaks and t - peaks[-1][0] < min_gap:
            if over > peaks[-1][1]:
                peaks[-1] = (t, over)
            continue
        peaks.append((t, over))
    return peaks


def live_windows(chat: list[tuple[float, float]], audio: list[tuple[float, float]], total: float,
                 limit: int = 4, max_len: float = 60.0) -> list[tuple[float, float, float]]:
    """Combine chat + audio peaks into windows. Chat lags action by ~5-10s."""
    wins = [(t - 30, t + 8, s) for t, s in chat]
    wins += [(t - 20, t + 12, s / 3) for t, s in audio]
    # boost windows where both signals agree
    boosted = []
    for s, e, sc in merge_windows(wins):
        both = any(s <= t <= e for t, _ in chat) and any(s <= t <= e for t, _ in audio)
        boosted.append((s, e, sc * (1.5 if both else 1.0)))
    boosted.sort(key=lambda w: w[2], reverse=True)
    return [(*clamp_window(s, e, total, max_len), sc) for s, e, sc in boosted[:limit]]
