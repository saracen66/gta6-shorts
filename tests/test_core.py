import time

from gta6clips.db import DB, Candidate
from gta6clips.filters import streamer_allowed
from gta6clips.process import build_description
from gta6clips.publish import due
from gta6clips.render import ass_time, build_ass, caption_chunks, shift_words
from gta6clips.signals import audio_peaks, chat_peaks, heatmap_windows, live_windows, merge_windows


def test_merge_windows():
    assert merge_windows([(0, 10, 1), (12, 20, 3), (40, 50, 2)]) == [(0, 20, 3), (40, 50, 2)]


def test_heatmap_windows_picks_hottest_and_skips_intro():
    hm = [{"start_time": 1, "end_time": 5, "value": 1.0},          # intro, skipped
          {"start_time": 600, "end_time": 610, "value": 0.9},
          {"start_time": 1200, "end_time": 1210, "value": 0.7},
          {"start_time": 2000, "end_time": 2010, "value": 0.3}]
    w = heatmap_windows(hm, 0.5, 2, total=3600)
    assert [round(x[2], 1) for x in w] == [0.9, 0.7]
    assert w[0][0] == 580 and w[0][1] == 625


def test_chat_peaks_detects_spike_with_hype_words():
    msgs = [(t, "hi") for t in range(0, 300, 3)]
    msgs += [(150 + i * 0.2, "KEKW LMAO") for i in range(25)]
    peaks = chat_peaks(msgs)
    assert len(peaks) == 1 and 145 <= peaks[0][0] <= 160


def test_audio_peaks():
    series = [(i * 0.1, -30.0) for i in range(3000)]
    for i in range(1500, 1540):
        series[i] = (i * 0.1, -10.0)
    peaks = audio_peaks(series)
    assert len(peaks) == 1 and 150 <= peaks[0][0] <= 155


def test_live_windows_boost_when_both_agree():
    w = live_windows(chat=[(100, 3.0), (400, 3.0)], audio=[(95, 12.0)], total=600)
    assert w[0][0] < 100 < w[0][1]


def test_caption_chunks_and_ass():
    words = [(0.0, 0.3, "no"), (0.3, 0.6, "way"), (0.6, 0.9, "bro"), (0.9, 1.2, "what"), (3.0, 3.3, "lol")]
    chunks = caption_chunks(words)
    assert [c[2] for c in chunks] == ["no way bro", "what", "lol"]
    ass = build_ass(10, "HOOK {x}", "twitch.tv/x", "@me", words)
    assert "HOOK (X)" in ass and "Style: Cap" in ass and ass.count("Dialogue:") == 6
    assert ass_time(65.5) == "0:01:05.50"


def test_shift_words():
    assert shift_words([(5, 6, "a"), (12, 13, "b")], 4, 10) == [(1, 2, "a")]


def test_streamer_filters():
    cfg = {"streamers": {"mode": "blocklist", "blocklist": ["twitch:Bad"]}}
    assert not streamer_allowed(cfg, "twitch", "bad")
    assert streamer_allowed(cfg, "kick", "bad")
    cfg = {"streamers": {"mode": "allowlist", "allowlist": ["kick:good"]}}
    assert streamer_allowed(cfg, "kick", "Good") and not streamer_allowed(cfg, "twitch", "x")


def test_description_has_credit_disclaimer_and_dedup_tags():
    cfg = {"post": {"hashtags": ["#GTA6", "#Gaming"], "disclaimer": "Belongs to streamer.",
                    "description_template": "{caption}\n{streamer} {streamer_url}\n{source_url}\n{disclaimer}\n{hashtags}"}}
    c = Candidate(id="x", source="twitch_clip", platform="twitch", url="u", source_url="https://twitch.tv/videos/1",
                  streamer="Streamer", streamer_url="https://twitch.tv/streamer")
    d = build_description(cfg, c, "Cop chase goes wrong", ["gta6", "copchase"])
    assert "https://twitch.tv/streamer" in d and "Belongs to streamer." in d
    assert d.lower().count("#gta6") == 1 and "#copchase" in d


def test_db_dedupe_and_rate_limits(tmp_path):
    db = DB(tmp_path / "t.db")
    base = dict(source="twitch_clip", platform="twitch", url="u", source_url="s", streamer="A", streamer_url="x")
    assert db.add_candidate(Candidate(id="1", moment_ts=1000, **base))
    assert not db.add_candidate(Candidate(id="2", moment_ts=1030, **base))   # same moment, other clipper
    assert db.add_candidate(Candidate(id="3", moment_ts=2000, **base))
    db.add_clip("1", "f.mp4", {"score": 8, "post_title": "t", "hashtags": []}, "d", ["youtube"])
    pc = {"max_per_day": 1, "min_gap_minutes": 60}
    assert due(db, "youtube", pc)
    db.mark_post("1", "youtube", "posted", remote_id="abc")
    assert not due(db, "youtube", pc)
    assert due(db, "youtube", pc, now=time.time() + 86401)
