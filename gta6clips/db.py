from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,            -- source:native_id[:start]
    source TEXT NOT NULL,           -- twitch_clip | kick_clip | youtube_vod | live
    platform TEXT NOT NULL,         -- twitch | kick | youtube
    url TEXT NOT NULL,              -- media URL to download
    source_url TEXT NOT NULL,       -- link shown to viewers (stream / VOD)
    streamer TEXT NOT NULL,
    streamer_url TEXT NOT NULL,
    title TEXT,
    start REAL,                     -- seconds into media (NULL = whole file)
    end REAL,
    moment_ts REAL,                 -- unix time the moment happened (dedupe)
    signal REAL DEFAULT 0,          -- heuristic strength before judging
    meta TEXT DEFAULT '{}',
    status TEXT DEFAULT 'new',      -- new|judged|rejected|rendered|failed
    error TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_cand_status ON candidates(status, signal DESC);
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY REFERENCES candidates(id),
    file TEXT NOT NULL,
    verdict TEXT NOT NULL,
    score INTEGER,
    post_title TEXT,
    description TEXT,
    hashtags TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS posts (
    clip_id TEXT REFERENCES clips(id),
    platform TEXT,
    status TEXT DEFAULT 'queued',   -- queued|posted|failed|skipped
    remote_id TEXT,
    error TEXT,
    attempts INTEGER DEFAULT 0,
    posted_at REAL,
    PRIMARY KEY (clip_id, platform)
);
CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, at REAL);
"""


@dataclass
class Candidate:
    id: str
    source: str
    platform: str
    url: str
    source_url: str
    streamer: str
    streamer_url: str
    title: str = ""
    start: float | None = None
    end: float | None = None
    moment_ts: float | None = None
    signal: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)
    status: str = "new"


class DB:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---- seen keys (VODs already scanned, etc.)
    def seen(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone() is not None

    def mark_seen(self, key: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO seen VALUES (?, ?)", (key, time.time()))

    # ---- candidates
    def is_duplicate(self, c: Candidate, window: float = 60.0) -> bool:
        if self.conn.execute("SELECT 1 FROM candidates WHERE id=?", (c.id,)).fetchone():
            return True
        if c.moment_ts is None:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM candidates WHERE platform=? AND lower(streamer)=lower(?) "
            "AND moment_ts IS NOT NULL AND abs(moment_ts-?) < ?",
            (c.platform, c.streamer, c.moment_ts, window),
        ).fetchone()
        return row is not None

    def add_candidate(self, c: Candidate) -> bool:
        if self.is_duplicate(c):
            return False
        with self.conn:
            self.conn.execute(
                "INSERT INTO candidates (id,source,platform,url,source_url,streamer,streamer_url,"
                "title,start,end,moment_ts,signal,meta,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (c.id, c.source, c.platform, c.url, c.source_url, c.streamer, c.streamer_url,
                 c.title, c.start, c.end, c.moment_ts, c.signal, json.dumps(c.meta), c.status,
                 time.time()),
            )
        return True

    def pending_candidates(self, limit: int) -> list[Candidate]:
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE status='new' ORDER BY signal DESC, created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._cand(r) for r in rows]

    def set_status(self, cid: str, status: str, error: str | None = None) -> None:
        with self.conn:
            self.conn.execute("UPDATE candidates SET status=?, error=? WHERE id=?", (status, error, cid))

    @staticmethod
    def _cand(r: sqlite3.Row) -> Candidate:
        return Candidate(
            id=r["id"], source=r["source"], platform=r["platform"], url=r["url"],
            source_url=r["source_url"], streamer=r["streamer"], streamer_url=r["streamer_url"],
            title=r["title"] or "", start=r["start"], end=r["end"], moment_ts=r["moment_ts"],
            signal=r["signal"] or 0, meta=json.loads(r["meta"] or "{}"), status=r["status"],
        )

    def get_candidate(self, cid: str) -> Candidate | None:
        r = self.conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        return self._cand(r) if r else None

    # ---- clips + posts
    def add_clip(self, cid: str, file: str, verdict: dict, description: str,
                 platforms: list[str]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO clips VALUES (?,?,?,?,?,?,?,?)",
                (cid, file, json.dumps(verdict), verdict.get("score"), verdict.get("post_title"),
                 description, " ".join(verdict.get("hashtags", [])), time.time()),
            )
            for p in platforms:
                self.conn.execute(
                    "INSERT OR IGNORE INTO posts (clip_id, platform) VALUES (?, ?)", (cid, p))
            self.conn.execute("UPDATE candidates SET status='rendered' WHERE id=?", (cid,))

    def queued_posts(self, platform: str) -> Iterator[sqlite3.Row]:
        return iter(self.conn.execute(
            "SELECT p.*, c.file, c.post_title, c.description, c.hashtags, c.score, "
            "k.streamer, k.platform AS src_platform FROM posts p "
            "JOIN clips c ON c.id=p.clip_id JOIN candidates k ON k.id=p.clip_id "
            "WHERE p.platform=? AND p.status='queued' AND p.attempts < 3 "
            "ORDER BY c.score DESC, c.created_at ASC", (platform,),
        ).fetchall())

    def posts_since(self, platform: str, since: float) -> int:
        return self.conn.execute(
            "SELECT count(*) FROM posts WHERE platform=? AND status='posted' AND posted_at>=?",
            (platform, since),
        ).fetchone()[0]

    def last_post_time(self, platform: str) -> float:
        r = self.conn.execute(
            "SELECT max(posted_at) FROM posts WHERE platform=? AND status='posted'", (platform,)
        ).fetchone()[0]
        return r or 0.0

    def mark_post(self, clip_id: str, platform: str, status: str,
                  remote_id: str | None = None, error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE posts SET status=?, remote_id=coalesce(?, remote_id), error=?, "
                "attempts=attempts+1, posted_at=CASE WHEN ?='posted' THEN ? ELSE posted_at END "
                "WHERE clip_id=? AND platform=?",
                (status, remote_id, error, status, time.time(), clip_id, platform),
            )

    def requeue_failed_attempt(self, clip_id: str, platform: str, error: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE posts SET attempts=attempts+1, error=?, "
                "status=CASE WHEN attempts+1>=3 THEN 'failed' ELSE status END "
                "WHERE clip_id=? AND platform=?",
                (error, clip_id, platform))

    def skip_streamer_posts(self, platform: str, streamer: str) -> list[sqlite3.Row]:
        """Cancel queued posts for a streamer; return already-posted ones (need manual removal)."""
        with self.conn:
            self.conn.execute(
                "UPDATE posts SET status='skipped' WHERE status='queued' AND clip_id IN "
                "(SELECT id FROM candidates WHERE platform=? AND lower(streamer)=lower(?))",
                (platform, streamer))
            self.conn.execute(
                "UPDATE candidates SET status='rejected', error='blocked streamer' "
                "WHERE status='new' AND platform=? AND lower(streamer)=lower(?)", (platform, streamer))
        return self.conn.execute(
            "SELECT p.* FROM posts p JOIN candidates k ON k.id=p.clip_id "
            "WHERE p.status='posted' AND k.platform=? AND lower(k.streamer)=lower(?)",
            (platform, streamer)).fetchall()

    def stats(self) -> dict[str, Any]:
        q = lambda sql: self.conn.execute(sql).fetchall()  # noqa: E731
        return {
            "candidates": {r[0]: r[1] for r in q("SELECT status, count(*) FROM candidates GROUP BY status")},
            "posts": {f"{r[0]}:{r[1]}": r[2] for r in q(
                "SELECT platform, status, count(*) FROM posts GROUP BY platform, status")},
        }
